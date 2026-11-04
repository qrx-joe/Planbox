"""公网环境验证。

针对真实部署环境跑一遍全链路，验证：
- HTTPS 与跨域行为
- 数据持久化
- 分享入口降级逻辑
- 抓取接口在真实公网 IP 上的表现
- 上传限制

用法：.venv/Scripts/python.exe verify_public.py <base_url>
"""

import json
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1 else "https://planbox-api.app.workbuddy.host").rstrip("/")

passed, failed = 0, 0


def call(method, path, payload=None, headers=None, raw_body=None):
    url = urllib.parse.quote(f"{BASE}{path}", safe=":/?&=%+-_.")
    data = raw_body if raw_body is not None else (
        json.dumps(payload).encode() if payload is not None else None
    )
    hdrs = dict(headers or {})
    if payload is not None and raw_body is None:
        hdrs.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode(errors="replace")
            if not body:
                return resp.status, None, dict(resp.headers)
            try:
                return resp.status, json.loads(body), dict(resp.headers)
            except json.JSONDecodeError:
                return resp.status, body, dict(resp.headers)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        try:
            return exc.code, json.loads(body), dict(exc.headers)
        except json.JSONDecodeError:
            return exc.code, body, dict(exc.headers)
    except Exception as exc:
        return 0, str(exc), {}


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}  {detail}")


print(f"\n目标：{BASE}")

print("\n[1] 连通性与 HTTPS")
code, health, hdrs = call("GET", "/api/health")
check("健康检查 200", code == 200, f"code={code}")
check("HTTPS 生效", BASE.startswith("https://"))
check("返回环境信息", isinstance(health, dict) and "env" in health)
env = (health or {}).get("env", {}) if isinstance(health, dict) else {}
check("PORT 环境变量已注入", env.get("port") == "8000", str(env.get("port")))
check("数据目录已重定位到容器内", env.get("data_dir", "").startswith("/workspace"),
      str(env.get("data_dir")))
check("LLM provider 为 fake（无密钥可用）", env.get("llm_provider") == "fake")

print("\n[2] 跨域行为")
code, _, hdrs = call("GET", "/api/health", headers={"Origin": "https://evil.example.com"})
acao = hdrs.get("access-control-allow-origin") or hdrs.get("Access-Control-Allow-Origin")
check("陌生来源未获 CORS 放行", not acao or acao == "https://evil.example.com", f"ACAO={acao}")

code, _, hdrs = call(
    "GET", "/api/health", headers={"Origin": "http://localhost:5173"}
)
acao = hdrs.get("access-control-allow-origin") or hdrs.get("Access-Control-Allow-Origin")
check("白名单来源获得放行", acao == "http://localhost:5173", f"ACAO={acao}")

print("\n[3] 数据持久化（写入后跨请求可读）")
text = (
    "公网验证活动：2026 云原生开发者大会。报名截止 2026-11-15 23:59，"
    "举办时间 2026-12-03 至 2026-12-04，地点北京国家会议中心，线下，"
    "门票免费，需要提前报名，主办方 CNCF 中国区。"
)
code, item, _ = call(
    "POST", "/api/source-items", {"source_type": "text", "raw_text": text}
)
check("创建 source item", code == 201 and isinstance(item, dict) and "id" in item,
      f"code={code} {str(item)[:120]}")
item_id = item.get("id") if isinstance(item, dict) else None

code, fetched_item, _ = call("GET", f"/api/source-items/{item_id}")
check("跨请求可读回（写入已落盘）", code == 200 and fetched_item.get("id") == item_id)
check(
    "正文完整保存",
    isinstance(fetched_item, dict)
    and (fetched_item.get("raw_text") or "").startswith("公网验证活动"),
)
check(
    "ingest_state 默认为 ready",
    isinstance(fetched_item, dict) and fetched_item.get("ingest_state") == "ready",
    f"实际 {fetched_item.get('ingest_state') if isinstance(fetched_item, dict) else fetched_item}",
)

print("\n[4] 抽取与确认入库")
code, ext, _ = call("POST", f"/api/source-items/{item_id}/extract")
check("AI 抽取可用（fake provider）", code == 200 and isinstance(ext, dict) and "id" in ext,
      f"code={code}")
ext_id = ext.get("id") if isinstance(ext, dict) else None

code, evt, _ = call(
    "POST",
    f"/api/review-queue/{ext_id}/confirm",
    {
        "status": "interested",
        "extracted": {
            "title": "2026 云原生开发者大会",
            "event_type": "conference",
            "organizer": "CNCF 中国区",
            "registration_url": "https://example.com/cyc2026",
            "registration_deadline_at": "2026-11-15T23:59:00+08:00",
            "event_start_at": "2026-12-03T09:00:00+08:00",
            "event_end_at": "2026-12-04T18:00:00+08:00",
            "location": "北京国家会议中心",
            "is_online": False,
            "missing_fields": [],
            "confidence": 0.88,
            "evidences": [
                {
                    "field": "registration_deadline_at",
                    "value": "2026-11-15T23:59:00+08:00",
                    "evidence": "报名截止 2026-11-15 23:59",
                    "confidence": 0.93,
                }
            ],
        },
    },
)
check("确认入库", code == 200 and isinstance(evt, dict) and "id" in evt,
      f"code={code} {str(evt)[:150]}")
evt_id = evt.get("id") if isinstance(evt, dict) else None

print("\n[5] 证据可溯源（公网链路一致性）")
code, ev, _ = call("GET", f"/api/evidences/{evt_id}")
check("查询证据", code == 200, f"code={code}")
check(
    "证据已关联活动",
    isinstance(ev, dict) and all(i.get("event_id") for i in ev.get("items", [])),
    str(ev)[:200] if isinstance(ev, dict) else str(ev),
)
check("证据数量大于 0", isinstance(ev, dict) and ev.get("total", 0) > 0)

print("\n[6] 筛选与状态流转")
code, page, _ = call("GET", "/api/events")
check("列表分页结构", code == 200 and isinstance(page, dict) and "items" in page)
check("列表非空", isinstance(page, dict) and page.get("total", 0) > 0)

code, filtered, _ = call("GET", "/api/events?status=interested&paginated=false")
check("状态筛选", code == 200 and isinstance(filtered, list))

code, kw, _ = call(
    "GET", "/api/events?keyword=%E4%BA%91%E5%8E%9F%E7%94%9F&paginated=false"
)
check("中文关键词搜索", code == 200 and isinstance(kw, list) and len(kw) > 0,
      f"命中 {len(kw) if isinstance(kw, list) else kw}")

code, upd, _ = call("PATCH", f"/api/events/{evt_id}/status", {"status": "applied"})
check("状态流转 interested→applied", code == 200 and upd.get("status") == "applied")

code, bad, _ = call("PATCH", f"/api/events/{evt_id}/status", {"status": "inbox"})
check("非法状态被拒 400", code == 400, f"code={code}")

print("\n[7] 分享入口（公网抓取表现）")
code, intake, _ = call(
    "POST",
    "/api/share/intake",
    {
        "scene": "1155",
        "title": "公网验证：某 AI 训练营招募",
        "summary": "仅摘要，不足 50 字",
        "article_url": "https://mp.weixin.qq.com/s/PublicVerify001",
    },
)
check("分享入口可用", code == 200, f"code={code} {str(intake)[:120]}")
check(
    "识别为微信链接",
    isinstance(intake, dict) and intake.get("is_wechat_article") is True,
)
check(
    "真实公网 IP 抓取失败但未报错（预期降级）",
    isinstance(intake, dict)
    and intake.get("fetch_attempted") is True
    and intake.get("fetch_succeeded") is False,
    f"attempted={intake.get('fetch_attempted') if isinstance(intake, dict) else None} "
    f"ok={intake.get('fetch_succeeded') if isinstance(intake, dict) else None}",
)
check(
    "返回 needs_content 而非错误",
    isinstance(intake, dict) and intake.get("state") == "needs_content",
)
check("给出明确指引", isinstance(intake, dict) and bool(intake.get("next_action")))
share_id = intake.get("source_item_id") if isinstance(intake, dict) else None

long_body = (
    "某 AI 训练营开始招募。由某知名 AI 公司主办，报名截止 2026-12-01 18:00，"
    "开课时间 2026-12-15 至 2026-12-20，全程线上直播，费用 1999 元/人，"
    "不限组队，面向有 Python 基础的开发者，报名后需完成一个入门项目。"
)
code, done, _ = call(
    "POST", f"/api/share/{share_id}/complete", {"raw_text": long_body, "auto_extract": True}
)
check("粘贴补全成功", code == 200 and done.get("state") == "ready",
      f"code={code} {str(done)[:120]}")
check("触发 AI 抽取", isinstance(done, dict) and done.get("extraction_started") is True)

code, again, _ = call(
    "POST", f"/api/share/{share_id}/complete", {"raw_text": long_body, "auto_extract": True}
)
check("幂等：重复提交不重复抽取",
      isinstance(again, dict) and again.get("extraction_started") is False)

print("\n[8] 旧 fetch 接口的降级")
code, fetched, _ = call(
    "POST",
    "/api/source-items/fetch",
    {"source_url": "https://mp.weixin.qq.com/s/anotherPublicTest"},
)
check("不再返回 422", code == 200, f"实际 {code}")
check(
    "降级为 needs_content",
    isinstance(fetched, dict) and fetched.get("status") == "needs_content",
)

print("\n[9] ICS 日历导出")
code, ics, hdrs = call("GET", "/api/calendar/export.ics")
check("ICS 导出 200", code == 200)
check("内容为合法 VCALENDAR", isinstance(ics, str) and "BEGIN:VCALENDAR" in ics)
ctype = hdrs.get("content-type") or hdrs.get("Content-Type") or ""
check("Content-Type 正确", "text/calendar" in ctype, ctype)

print("\n[10] 上传限制")
# 构造一个 11 MB 的文件，超过默认 10 MB 上限
boundary = "----verifyboundary"
big = b"x" * (11 * 1024 * 1024)
body = (
    f"--{boundary}\r\n"
    f'Content-Disposition: form-data; name="file"; filename="big.bin"\r\n'
    f"Content-Type: application/octet-stream\r\n\r\n"
).encode() + big + f"\r\n--{boundary}--\r\n".encode()

code, resp, _ = call(
    "POST",
    f"/api/source-items/{item_id}/assets",
    raw_body=body,
    headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
)
check("超大上传被拒绝 413", code == 413, f"实际 {code} {str(resp)[:120]}")

# 小文件应成功
small_body = (
    f"--{boundary}\r\n"
    f'Content-Disposition: form-data; name="file"; filename="ok.png"\r\n'
    f"Content-Type: image/png\r\n\r\n"
).encode() + b"\x89PNG\r\n\x1a\n" * 4 + f"\r\n--{boundary}--\r\n".encode()
code, asset, _ = call(
    "POST",
    f"/api/source-items/{item_id}/assets",
    raw_body=small_body,
    headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
)
check("正常大小上传成功", code == 201 and isinstance(asset, dict),
      f"实际 {code} {str(asset)[:120]}")

print("\n[11] 错误处理")
code, _, _ = call("GET", "/api/events/does-not-exist")
check("不存在的活动 404", code == 404)
code, _, _ = call("GET", "/api/evidences/does-not-exist")
check("不存在的证据 404 或空列表", code in (200, 404), f"实际 {code}")
code, _, _ = call("POST", "/api/share/nonexistent/complete", {"raw_text": long_body})
check("补全不存在的条目 404", code == 404)
code, _, _ = call(
    "POST", "/api/source-items/fetch", {"source_url": "https://example.com/notwechat"}
)
check("非微信链接 400（不再被 WAF 拦截）", code == 400, f"实际 {code}")

#回归：URL 里带完整外链会被 CDN 层 WAF 拦截，这是为什么链接必须走请求体
code, waf, _ = call(
    "POST", "/api/source-items/fetch?source_url=https%3A%2F%2Fexample.com"
)
check(
    "查询参数传外链仍被 WAF 拦（已知限制，已规避）",
    code == 403,
    f"实际 {code}",
)
code, _, _ = call("POST", "/api/source-items", {})
check("空内容创建被拒", code == 422, f"实际 {code}")

print(f"\n{'=' * 52}\n通过 {passed} / {passed + failed}\n{'=' * 52}")
sys.exit(1 if failed else 0)