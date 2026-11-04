"""真实模型抽取质量评估：读 golden_set.json 逐条调用真实 Provider，输出字段级指标。

小规模授权边界：顺序执行；单条失败（网络/5xx/JSON 或 schema 解析）至多重试 1 次；
连续 2 条彻底失败立即终止。默认上限 9 条样本（黄金集全部），可 --only/--limit 缩小。
不打印 API Key 与样本正文，只输出样本 id 与判定结果。报告写入 evals/report-*.json。

用法：
  uv run --locked python evals/run_eval.py --dry-run   # 不调模型，校验金标准与判定逻辑
  uv run --locked python evals/run_eval.py --only s6   # 单条冒烟（1 次请求）
  uv run --locked python evals/run_eval.py             # 全量（≤9 条 + 重试）
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

EVAL_DIR = Path(__file__).parent
sys.path.insert(0, str(EVAL_DIR.parent))  # 脚本目录不含包根，显式加入
FULL_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")
FULL_DATETIME = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")


def load_cases() -> list[dict]:
    data = json.loads((EVAL_DIR / "golden_set.json").read_text(encoding="utf-8"))
    cases = data["cases"]
    for case in cases:
        if case.get("text_file"):
            case["text"] = (EVAL_DIR / case["text_file"]).read_text(encoding="utf-8")
        assert case.get("text"), f"样本 {case['id']} 缺正文"
    return cases


def judge(value: object, rule: dict) -> dict:
    """按金标准规则判定单字段，返回 {verdict: ok|miss|fabricated|violation, note}。"""
    mode = rule["mode"]
    if mode == "observe":
        return {"verdict": "observe", "note": rule.get("note", "")}

    def text_of(v: object) -> str:
        return str(v).strip() if v is not None else ""

    if mode == "nullable":
        if value is None or text_of(value) == "":
            return {"verdict": "ok", "note": "null"}
        return {"verdict": "fabricated", "note": f"原文无依据却输出 {text_of(value)[:40]}"}

    if mode == "nullable_or_contains":
        if value is None or text_of(value) == "":
            return {"verdict": "ok", "note": "null"}
        if any(k in text_of(value) for k in rule["accept"]):
            return {"verdict": "ok", "note": text_of(value)[:40]}
        return {"verdict": "fabricated", "note": f"值无原文依据 {text_of(value)[:40]}"}

    if mode == "exact":
        if value in rule["accept"]:
            return {"verdict": "ok", "note": str(value)}
        return {"verdict": "miss", "note": f"期望 {rule['accept']} 实得 {value}"}

    if mode == "contains":
        value_text = text_of(value)
        if value_text and all(k in value_text for k in rule["accept"]):
            return {"verdict": "ok", "note": value_text[:40]}
        return {"verdict": "miss",
                "note": f"期望含 {rule['accept']} 实得 {value_text[:40] or 'null'}"}

    if mode == "contains_any":
        value_text = text_of(value)
        if value_text and any(k in value_text for k in rule["accept"]):
            return {"verdict": "ok", "note": value_text[:40]}
        return {"verdict": "miss",
                "note": f"期望含任一 {rule['accept']} 实得 {value_text[:40] or 'null'}"}

    if mode == "date":
        value_text = text_of(value)
        if value_text[:10] in rule["accept"] and FULL_DATE.match(value_text):
            return {"verdict": "ok", "note": value_text[:10]}
        return {"verdict": "miss",
                "note": f"期望日期 {rule['accept']} 实得 {value_text[:40] or 'null'}"}

    if mode == "datetime":
        value_text = text_of(value)
        try:
            got = datetime.fromisoformat(value_text)
        except ValueError:
            return {"verdict": "miss", "note": f"非时刻格式 {value_text[:40]}"}
        for accepted in rule["accept"]:
            if got == datetime.fromisoformat(accepted):
                return {"verdict": "ok", "note": value_text[:40]}
        return {"verdict": "miss", "note": f"期望时刻 {rule['accept']} 实得 {value_text[:40]}"}

    if mode == "time_discipline":
        if value is None or text_of(value) == "":
            return {"verdict": "ok", "note": "null（不补全）"}
        value_text = text_of(value)
        if FULL_DATETIME.search(value_text) or FULL_DATE.match(value_text):
            return {"verdict": "violation",
                    "note": f"补全了完整日期 {value_text[:40]}，应保留原片段或 null"}
        if not rule.get("accept_fragments") or any(
            frag in value_text for frag in rule["accept_fragments"]
        ):
            return {"verdict": "ok", "note": f"保留原片段 {value_text[:40]}"}
        return {"verdict": "violation", "note": f"片段无依据 {value_text[:40]}"}

    return {"verdict": "observe", "note": f"未知 mode {mode}"}


async def run(limit: int | None, only: str | None, dry_run: bool) -> int:
    from backend.app.core.config import settings

    cases = load_cases()
    if only:
        cases = [case for case in cases if case["id"] == only]
        assert cases, f"--only {only} 不在黄金集内"
    if limit:
        cases = cases[:limit]

    provider = None
    if not dry_run:
        if settings.llm_provider != "openai" or not settings.openai_api_key:
            print("未配置真实模型：请在 .env 设置 LLM_PROVIDER=openai 与 OPENAI_API_KEY"
                  "（StepFun 见 .env.example）。", file=sys.stderr)
            return 2
        from backend.app.providers.llm import OpenAIProvider

        provider = OpenAIProvider(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
            model=settings.openai_model,
        )
        print(f"model={provider.model} base={provider.base_url} cases={len(cases)}")

    results, consecutive_failures, total_requests = [], 0, 0
    for case in cases:
        fields, latency, usage, error = {}, None, None, None
        if dry_run:
            fields = {"title": "dry-run（不调模型）"}
        else:
            started = time.monotonic()
            attempts = 0
            while attempts < 2:  # 单条至多重试 1 次
                attempts += 1
                total_requests += 1
                try:
                    draft = await provider.extract_event(case["text"], case.get("url"))
                    error = None
                    break
                except Exception as exc:  # 网络/HTTP/解析/schema 失败统一计入重试
                    error = f"{type(exc).__name__}: {str(exc)[:120]}"
            latency = round(time.monotonic() - started, 2)
            usage = provider.last_usage
            if error is not None:
                consecutive_failures += 1
                results.append({"id": case["id"], "error": error,
                                "latency_s": latency, "attempts": attempts})
                print(f"[{case['id']}] 失败（第 {attempts} 次）: {error}")
                if consecutive_failures >= 2:
                    print("连续 2 条彻底失败，按授权边界终止执行。", file=sys.stderr)
                    break
                continue
            consecutive_failures = 0
            fields = draft.model_dump()

        judged = {}
        for field, rule in case["expected"].items():
            judged[field] = {"value": fields.get(field), **judge(fields.get(field), rule)}
        results.append({"id": case["id"], "kind": case["kind"], "latency_s": latency,
                        "usage": usage, "fields": judged, "error": None})
        verdicts = {f: j["verdict"] for f, j in judged.items()}
        print(f"[{case['id']}] " + " ".join(
            f"{field}={verdict}" for field, verdict in verdicts.items()))

    # 汇总
    counters: dict[str, int] = {}
    field_stats: dict[str, dict[str, int]] = {}
    for item in results:
        for field, j in (item.get("fields") or {}).items():
            verdict = j["verdict"]
            counters[verdict] = counters.get(verdict, 0) + 1
            stats = field_stats.setdefault(field, {})
            stats[verdict] = stats.get(verdict, 0) + 1
    scored = counters.get("ok", 0) + counters.get("miss", 0) + counters.get("fabricated", 0) \
        + counters.get("violation", 0)
    latencies = sorted(item["latency_s"] for item in results if item.get("latency_s"))
    tokens = sum((item.get("usage") or {}).get("total_tokens", 0) for item in results)

    summary = {
        "model": provider.model if provider else "dry-run",
        "cases": len(results),
        "requests": total_requests,
        "field_verdicts": counters,
        "accuracy": round(counters.get("ok", 0) / scored, 3) if scored else None,
        "fabricated": counters.get("fabricated", 0),
        "time_discipline_violations": counters.get("violation", 0),
        "latency_p50_s": latencies[len(latencies) // 2] if latencies else None,
        "latency_max_s": latencies[-1] if latencies else None,
        "total_tokens": tokens,
        "per_field": field_stats,
    }
    print("\n===== 汇总 =====")
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    stamp = time.strftime("%Y%m%d-%H%M%S")
    report_path = EVAL_DIR / f"report-{stamp}.json"
    report_path.write_text(
        json.dumps({"summary": summary, "results": results}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    print(f"报告已写入 {report_path}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", help="只跑指定 id 的样本")
    parser.add_argument("--limit", type=int, help="只跑前 N 条")
    parser.add_argument("--dry-run", action="store_true", help="不调模型，只校验判定逻辑")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args.limit, args.only, args.dry_run)))
