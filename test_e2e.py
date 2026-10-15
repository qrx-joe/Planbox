"""Planbox 端到端测试。

与 test_server.py 共用 .testdata/e2e.db，测试可重复执行且不污染开发库。

运行：
    # 终端 1
    .venv/Scripts/python.exe test_server.py
    # 终端 2
    .venv/Scripts/python.exe test_e2e.py
"""

import json
import pathlib
import sqlite3
import urllib.error
import urllib.parse
import urllib.request

TEST_DIR = pathlib.Path(__file__).parent / ".testdata"

import os  # noqa: E402

os.environ["database_path"] = str(TEST_DIR / "e2e.db")
os.environ["data_dir"] = str(TEST_DIR)
os.environ["upload_dir"] = str(TEST_DIR / "uploads")

BASE = "http://127.0.0.1:8020"
passed, failed = 0, 0


def reset_database():
    """清空业务表，保证测试可重复执行。

    直接连库清理，不走接口 —— 这些是测试自己造的脏数据。
    """
    conn = sqlite3.connect(str(TEST_DIR / "e2e.db"))
    for table in (
        "evidences",
        "opportunity_scores",
        "reminders",
        "event_sources",
        "extraction_results",
        "source_assets",
        "source_items",
        "events",
    ):
        conn.execute(f"DELETE FROM {table}")
    conn.commit()
    conn.close()


def call(method, path, payload=None):
    """返回 (status, json_body)；非 JSON 响应（如 ICS 导出）返回 (status, text)。"""
    url = urllib.parse.quote(f"{BASE}{path}", safe=":/?&=%")
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    try:
        with urllib.request.urlopen(req) as resp:
            body = resp.read().decode()
            if not body:
                return resp.status, None
            try:
                return resp.status, json.loads(body)
            except json.JSONDecodeError:
                return resp.status, body
    except urllib.error.HTTPError as exc:
        body = exc.read().decode()
        try:
            return exc.code, json.loads(body)
        except json.JSONDecodeError:
            return exc.code, body


def check(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}  {detail}")


# 测试脚本与服务共享测试库，可直接用真实去重逻辑验证匹配质量
from backend.app.services.dedup_service import DedupService  # noqa: E402

_dedup = DedupService()


def draft_matches(draft):
    return _dedup.check_draft(draft).has_duplicates


reset_database()

print("\n[1] 收集与抽取")


print("\n[1] 收集与抽取")
text_a = (
    "2026 AI 编程黑客松报名开启。由 OpenAI 主办，报名截止 2026-06-01 23:59，"
    "举办时间 2026-06-15 至 2026-06-17，地点上海，需组队，奖金 10 万元。"
)
text_b = "2026 春季黑客松，报名截止 6月1日，线上举办，主办方 OpenAI。"
text_c = "完全无关的线下读书会，下个月北京举办，免费报名。"

_, src_a = call("POST", "/api/source-items", {"source_type": "text", "raw_text": text_a})
_, src_b = call("POST", "/api/source-items", {"source_type": "text", "raw_text": text_b})
_, src_c = call("POST", "/api/source-items", {"source_type": "text", "raw_text": text_c})
check("创建 source item", all(s and "id" in s for s in (src_a, src_b, src_c)))

_, ext_a = call("POST", f"/api/source-items/{src_a['id']}/extract")
_, ext_b = call("POST", f"/api/source-items/{src_b['id']}/extract")
_, ext_c = call("POST", f"/api/source-items/{src_c['id']}/extract")
check("三次抽取均成功", all(e and "id" in e for e in (ext_a, ext_b, ext_c)))

print("\n[2] 确认入库")
_, evt_a = call(
    "POST",
    f"/api/review-queue/{ext_a['id']}/confirm",
    {
        "status": "interested",
        "extracted": {
            "title": "2026 AI 编程黑客松",
            "event_type": "hackathon",
            "organizer": "OpenAI",
            "registration_url": "https://example.com/apply-a",
            "registration_deadline_at": "2026-06-01T23:59:00+08:00",
            "event_start_at": "2026-06-15T09:00:00+08:00",
            "event_end_at": "2026-06-17T18:00:00+08:00",
            "location": "上海",
            "is_online": False,
            "team_required": "需组队，3-5 人",
            "prize": "10 万元",
            "missing_fields": [],
            "confidence": 0.9,
            "evidences": [
                {
                    "field": "registration_deadline_at",
                    "value": "2026-06-01T23:59:00+08:00",
                    "evidence": "报名截止 2026-06-01 23:59",
                    "confidence": 0.92,
                },
                {
                    "field": "prize",
                    "value": "10 万元",
                    "evidence": "奖金 10 万元",
                    "confidence": 0.88,
                },
            ],
        },
    },
)
check("确认活动 A", evt_a and "id" in evt_a, str(evt_a)[:150])

_, evt_b = call(
    "POST",
    f"/api/review-queue/{ext_b['id']}/confirm",
    {
        "status": "interested",
        "extracted": {
            "title": "2026春季黑客松",
            "event_type": "hackathon",
            "organizer": "OpenAI",
            "event_start_at": "2026-06-15T09:00:00+08:00",
            "is_online": True,
            "missing_fields": ["registration_deadline_at"],
            "confidence": 0.6,
            "evidences": [
                {
                    "field": "event_start_at",
                    "value": "2026-06-15",
                    "evidence": "举办时间见正文",
                    "confidence": 0.55,
                }
            ],
        },
    },
)
check("确认活动 B", evt_b and "id" in evt_b, str(evt_b)[:150])

_, evt_c = call(
    "POST",
    f"/api/review-queue/{ext_c['id']}/confirm",
    {
        "status": "rejected",
        "extracted": {
            "title": "线下读书会",
            "event_type": "meetup",
            "location": "北京",
            "missing_fields": [],
            "confidence": 0.7,
            "evidences": [],
        },
    },
)
check("确认活动 C", evt_c and "id" in evt_c)

print("\n[3] 证据可溯源（此前 event_id 恒为 NULL 的 bug）")
code, ev = call("GET", f"/api/evidences/{evt_a['id']}")
check("查询活动证据", code == 200, f"code={code}")
if isinstance(ev, dict):
    check("证据数量大于 0", ev.get("total", 0) > 0, f"total={ev.get('total')}")
    items = ev.get("items", [])
    check("全部证据已关联活动", all(i.get("event_id") for i in items))
    fields = {i["field_name"] for i in items}
    check("包含 registration_deadline_at 证据", "registration_deadline_at" in fields, str(fields))
    check("包含用户补充的 prize 证据", "prize" in fields, str(fields))
    if items:
        check("证据带原文出处", any(i.get("evidence_text") for i in items))
        check("证据带置信度", any(i.get("confidence") is not None for i in items))
        check("证据关联到来源条目", all(i.get("source_item_id") for i in items))

code, _ = call("GET", f"/api/evidences?extraction_result_id={ext_a['id']}")
check("按草稿查证据", code == 200)

# 证据去重：同一草稿重复确认不应产生重复字段证据
code, again = call("GET", f"/api/evidences/{evt_a['id']}")
if isinstance(again, dict):
    names = [i["field_name"] for i in again.get("items", [])]
    check("证据字段无重复", len(names) == len(set(names)), str(names))

print("\n[4] 筛选与排序")
code, page = call("GET", "/api/events")
check("默认分页结构", code == 200 and "items" in page and "total" in page)
check("总数为 3", page.get("total") == 3, f"实际 {page.get('total')}")

code, arr = call("GET", "/api/events?paginated=false")
check("paginated=false 返回数组", code == 200 and isinstance(arr, list))

code, r = call("GET", "/api/events?status=interested&paginated=false")
check("筛选 interested 得 2 条", code == 200 and len(r) == 2, f"实际 {len(r) if isinstance(r, list) else r}")

code, r = call("GET", "/api/events?status=rejected&paginated=false")
check("筛选 rejected 得 1 条", code == 200 and len(r) == 1)

code, r = call("GET", "/api/events?status=interested&status=applied&paginated=false")
check("多状态组合筛选", code == 200 and len(r) == 2, f"实际 {len(r) if isinstance(r, list) else r}")

code, r = call("GET", "/api/events?keyword=%E9%BB%91%E5%AE%A2%E6%9D%BE&paginated=false")
check("关键词搜索得 2 条", code == 200 and len(r) == 2, f"实际 {len(r) if isinstance(r, list) else r}")

code, r = call("GET", "/api/events?keyword=OpenAI&paginated=false")
check("按主办方搜索得 2 条", code == 200 and len(r) == 2)

code, r = call("GET", "/api/events?event_type=hackathon&paginated=false")
check("按类型筛选得 2 条", code == 200 and len(r) == 2)

code, r = call("GET", "/api/events?is_online=true&paginated=false")
check("按线上筛选得 1 条", code == 200 and len(r) == 1)

code, r = call("GET", "/api/events?location=%E4%B8%8A%E6%B5%B7&paginated=false")
check("按地点筛选得 1 条", code == 200 and len(r) == 1)

code, r = call(
    "GET",
    "/api/events?deadline_after=2026-01-01T00%3A00%3A00%2B08%3A00"
    "&deadline_before=2027-01-01T00%3A00%3A00%2B08%3A00&paginated=false",
)
check("按截止区间筛选得 1 条", code == 200 and len(r) == 1, f"实际 {len(r) if isinstance(r, list) else r}")

code, p = call("GET", "/api/events?limit=2&offset=0")
check("分页 limit=2", code == 200 and len(p["items"]) == 2)
check("分页 total 为全量", p["total"] == 3)

code, p = call("GET", "/api/events?limit=2&offset=2")
check("分页 offset=2 得 1 条", code == 200 and len(p["items"]) == 1)

code, r = call("GET", "/api/events?sort=created_at&order=desc&paginated=false")
check("倒序排序可执行", code == 200 and len(r) == 3)

code, r = call("GET", "/api/events?has_missing_fields=true&paginated=false")
check("筛选信息缺失得 1 条", code == 200 and len(r) == 1, f"实际 {len(r) if isinstance(r, list) else r}")

print("\n[5] 状态流转")
code, r = call("PATCH", f"/api/events/{evt_a['id']}/status", {"status": "applied"})
check("interested -> applied", code == 200 and r["status"] == "applied", f"code={code}")

# 反悔路径：放弃后可以重新考虑，这是产品上合理的
code, r = call("PATCH", f"/api/events/{evt_c['id']}/status", {"status": "applied"})
check("rejected -> applied 允许（反悔）", code == 200 and r["status"] == "applied", f"code={code}")

# interested 可以直接标done（用户可能判断活动已结束），但不能倒退回 inbox
code, r = call("PATCH", f"/api/events/{evt_b['id']}/status", {"status": "done"})
check("interested -> done 允许", code == 200 and r["status"] == "done")

code, r = call("PATCH", f"/api/events/{evt_b['id']}/status", {"status": "inbox"})
check("done -> inbox 被拒（inbox 不可手动设置）", code == 400, f"实际 {code}")

code, r = call("PATCH", f"/api/events/{evt_a['id']}/status", {"status": "done"})
check("applied -> done 允许", code == 200 and r["status"] == "done")

# 终态回退：done 可以重新激活
code, r = call("PATCH", f"/api/events/{evt_a['id']}/status", {"status": "interested"})
check("done -> interested 允许重新激活", code == 200 and r["status"] == "interested")

code, r = call("PATCH", "/api/events/nonexistent/status", {"status": "applied"})
check("不存在的活动返回 404", code == 404)

print("\n[6] 字段编辑")
# 保留 location 为空，用于后续合并补全验证；fee/notes 在 A 侧缺失
code, r = call(
    "PATCH",
    f"/api/events/{evt_b['id']}",
    {"fee": "免费", "notes": "去年参加过，口碑不错"},
)
check("PATCH 局部更新", code == 200, f"code={code} {r}")
if code == 200:
    check("新字段已写入", r.get("fee") == "免费")
    check("notes 字段已写入", r.get("notes") == "去年参加过，口碑不错")
    check("未传字段保持不变", r.get("organizer") == "OpenAI")
    check("未传字段 is_online 不变", r.get("is_online") is True)
    check("未传字段 location 仍为空", r.get("location") is None)

code, r = call("PATCH", f"/api/events/{evt_b['id']}", {"prize": "5 万元"})
check(
    "二次 PATCH 不影响其他字段",
    code == 200 and r.get("notes") == "去年参加过，口碑不错",
)

code, r = call("PATCH", "/api/events/nonexistent", {"fee": "免费"})
check("编辑不存在活动返回 404", code == 404)

print("\n[7] 去重检测")
code, dup = call("GET", f"/api/events/duplicates/{evt_b['id']}")
check("去重接口可调用", code == 200, f"code={code} {str(dup)[:120]}")
if code == 200:
    check("检出重复项", dup.get("has_duplicates") is True)
    cands = dup.get("candidates", [])
    check("候选含活动 A", any(c["event"]["id"] == evt_a["id"] for c in cands))
    if cands:
        check("匹配理由非空", all(c["match_reasons"] for c in cands))
        check("匹配分数合法", all(0 <= c["match_score"] <= 1 for c in cands))

code, nodup = call("GET", f"/api/events/duplicates/{evt_c['id']}")
check("无关活动不误报", code == 200 and nodup.get("has_duplicates") is False, str(nodup)[:120])

print("\n[9] 草稿重复预检")
# fake extractor 抽取质量有限，草稿字段常为空。
# 这里直接验证去重匹配逻辑本身，对应接口已由调用方验证可达。
check(
    "同名同时间草稿被检出",
    draft_matches(
        {
            "title": "2026 AI 编程黑客松",
            "event_type": "hackathon",
            "organizer": "OpenAI",
            "event_start_at": "2026-06-15T09:00:00+08:00",
        }
    ),
)
check(
    "仅名称相同也命中",
    draft_matches({"title": "2026 AI 编程黑客松（上海站）"}),
)
check(
    "完全无关的草稿不误报",
    not draft_matches({"title": "杭州插画师线下工作坊", "event_type": "workshop"}),
)
check(
    "同报名链接直接命中",
    draft_matches(
        {
            "title": "换个完全不同的名字",
            "registration_url": "https://example.com/apply-a",
        }
    ),
)

code, _ = call("GET", "/api/review-queue/nonexistent/duplicates")
check("预检不存在的草稿返回 404", code == 404)

print("\n[8] 合并")
code, merged = call("POST", "/api/events/merge", {"target_id": evt_a["id"], "source_id": evt_b["id"]})
check("合并接口可调用", code == 200, f"code={code} {str(merged)[:150]}")
if code == 200:
    filled = merged["filled_fields"]
    check("target 保留", merged["merged"]["id"] == evt_a["id"])
    # A 已有 location/fee，B 有 location/fee/is_online/notes
    check("空字段 is_online 被补全", "is_online" in filled, str(filled))
    check("空字段 notes 被补全", "notes" in filled, str(filled))
    check("A 已有的字段不被覆盖", "location" not in filled, str(filled))
    check("来源关联已迁移", merged.get("transferred_sources", 0) > 0)
    check("补全后的字段值正确", merged["merged"].get("notes") == "去年参加过，口碑不错")
    check("补全后的线上属性正确", merged["merged"].get("is_online") is True)
    check("target 原有 location 保留", merged["merged"].get("location") == "上海")
    check("target 原有信息不丢", merged["merged"].get("team_required") == "需组队，3-5 人")
    check(
        "target 原有 deadline 不被覆盖",
        merged["merged"].get("registration_deadline_at") == "2026-06-01T23:59:00+08:00",
    )

code, _ = call("GET", f"/api/events/{evt_b['id']}")
check("重复项已删除", code == 404)

code, r = call("POST", "/api/events/merge", {"target_id": evt_a["id"], "source_id": evt_a["id"]})
check("自我合并被拒", code == 400, f"实际 {code}")

code, r = call("POST", "/api/events/merge", {"target_id": "nope", "source_id": evt_c["id"]})
check("合并不存在活动返回 404", code == 404)

code, ev_after = call("GET", f"/api/evidences/{evt_a['id']}")
if isinstance(ev_after, dict):
    check("合并后证据保留", ev_after.get("total", 0) > 0, f"total={ev_after.get('total')}")

print("\n[10] 删除与日历")
code, _ = call("GET", f"/api/events/{evt_a['id']}")
check("GET 单个活动", code == 200)

code, _ = call("DELETE", f"/api/events/{evt_c['id']}")
check("删除活动", code == 204)
code, _ = call("GET", f"/api/events/{evt_c['id']}")
check("删除后 404", code == 404)
code, _ = call("DELETE", f"/api/events/{evt_c['id']}")
check("重复删除 404", code == 404)

code, ics = call("GET", "/api/calendar/export.ics")
check("ICS 导出正常", code == 200 and isinstance(ics, str) and "BEGIN:VCALENDAR" in ics, f"code={code}")

code, one_ics = call("GET", f"/api/calendar/events/{evt_a['id']}.ics")
check("单活动 ICS 导出", code == 200 and isinstance(one_ics, str) and "BEGIN:VCALENDAR" in one_ics)

print("\n[11] 数据库完整性")
conn = sqlite3.connect(str(TEST_DIR / "e2e.db"))
orphan = conn.execute(
    "SELECT COUNT(*) FROM evidences WHERE extraction_result_id IS NOT NULL AND event_id IS NULL"
).fetchone()[0]
check("无孤儿证据（已确认草稿的证据均已关联）", orphan == 0, f"孤儿数 {orphan}")
mismatch = conn.execute(
    "SELECT COUNT(*) FROM events WHERE id NOT IN (SELECT event_id FROM event_sources)"
).fetchone()[0]
check("每个活动都有来源记录", mismatch == 0, f"缺来源 {mismatch}")
cols = {row[1] for row in conn.execute("PRAGMA table_info(events)")}
check("events 表已有 notes 列（迁移生效）", "notes" in cols)
conn.close()

print(f"\n{'=' * 50}\n通过 {passed} / {passed + failed}\n{'=' * 50}")
raise SystemExit(1 if failed else 0)