"""证据读取投影：只核验原文位置和值关系，不推断语义真假，不写入历史。"""

import json

from backend.app.core.evidence import FACT_FIELDS, locate_evidence
from backend.app.core.temporal import precise_epoch
from backend.app.schemas.evidence import EvidenceRead


def evidence_to_read(row: dict[str, object]) -> EvidenceRead:
    """从已关联来源/活动的行生成响应；偏移按原始正文 Unicode 字符计，左闭右开。

    旧 evidence_text 不保证是原文，故按 field_value 重新定位；图片/无正文保持未证实。
    值相同也不代表已核实；不同只表示与当前值不一致，不能自动认定旧值曾生效。
    不返回整篇正文，不更新数据库，旧 confidence 仅原样保留而非校准概率。
    """
    result = EvidenceRead.model_validate(row)
    value = row.get("field_value")
    located = locate_evidence(str(value), str(row.get("source_text") or "")) if value else None
    result.evidence_text = located.quote if located else None
    result.verification_status = "located" if located else "unverified"
    result.start_offset = located.start_offset if located else None
    result.end_offset = located.end_offset if located else None
    is_draft = not row.get("current_event_id") and not row.get("event_id")
    if not row.get("current_event_id"):
        if not is_draft or not row.get("current_extracted_json"):
            result.value_relation = "draft" if is_draft else "unavailable"
            return result
        try:
            draft = json.loads(str(row["current_extracted_json"]))
            if not isinstance(draft, dict):
                raise ValueError
        except (TypeError, ValueError):
            result.value_relation = "unknown_field"
            return result
        row = {**row, **{f"current_{field}": draft.get(field) for field in FACT_FIELDS},
               "current_schedule_json": json.dumps(draft.get("schedule", []))}
    field = str(row["field_name"])
    if field in FACT_FIELDS:
        current = row.get("current_" + field)
        if field == "is_online" and current is not None:
            current = str(bool(current))
    elif field.startswith("schedule."):
        parts = field.split(".")
        try:
            if len(parts) != 3 or parts[2] not in {"name", "start_at", "end_at"}:
                raise ValueError
            index = int(parts[1])
            if index < 0:
                raise ValueError
            schedule = json.loads(str(row.get("current_schedule_json") or "[]"))
            current = schedule[index].get(parts[2])
        except (ValueError, TypeError, IndexError, KeyError, AttributeError):
            result.value_relation = "unknown_field"
            return result
    else:
        result.value_relation = "unknown_field"
        return result
    if current is None or current == "":
        result.value_relation = "no_current_value"
    else:
        matches = str(current) == value
        if field.endswith("_at"):
            current_epoch = precise_epoch(str(current))
            value_epoch = precise_epoch(str(value))
            if current_epoch is not None and value_epoch is not None:
                matches = current_epoch == value_epoch
        elif field == "is_online" and isinstance(value, str):
            matches = str(current).casefold() == value.casefold()
        result.value_relation = (
            "matches_current" if matches else "differs_from_current"
        )
    if is_draft:
        result.value_relation = {
            "matches_current": "matches_draft", "differs_from_current": "differs_from_draft",
            "no_current_value": "no_draft_value",
        }.get(result.value_relation, result.value_relation)
    return result
