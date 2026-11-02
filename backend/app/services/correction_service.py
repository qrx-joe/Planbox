"""人工更正/撤回用例；来源定位与人工决定分别表达。"""

import json

from backend.app.core.ids import new_id
from backend.app.core.temporal import TIME_FIELDS, validate_event_times
from backend.app.core.time import now_iso
from backend.app.repositories.correction_repository import (
    EDIT_COLUMNS,
    CorrectionRepository,
    field_value,
)
from backend.app.schemas.correction import CorrectionList, CorrectionRead
from backend.app.schemas.event import STATUS_TRANSITIONS, EventStatusError, EventUpdate


class CorrectionConflict(ValueError):
    """陈旧编辑、被后续更正覆盖或无法撤回；API 映射为 409。"""


def _can_retract(item: dict, rows: list[dict], current: dict) -> bool:
    if item["action"] != "correction" or item["original_event_id"] != current["id"]:
        return False
    field = item["field_name"]
    try:
        current_value = field_value(current, field)
    except (ValueError, TypeError):
        return False
    return (
        current_value == json.loads(item["new_value_json"])
        and not any(
            other["field_name"] == field and other["sequence"] > item["sequence"]
            for other in rows
        )
    )


class CorrectionService:
    def __init__(self) -> None:
        self.repository = CorrectionRepository()

    def list(self, event_id: str, limit: int = 50, offset: int = 0) -> CorrectionList:
        """分页返回审计；can_retract 表示整次操作未被覆盖，实际撤回还会校验区间。"""
        current, rows = self.repository.list(event_id)
        items = []
        for row in rows[offset:offset + limit]:
            group = [item for item in rows if item["operation_id"] == row["operation_id"]]
            items.append(CorrectionRead(
                **{key: value for key, value in row.items() if key not in {
                    "old_value_json", "new_value_json", "sequence",
                }},
                old_value=json.loads(str(row["old_value_json"])),
                new_value=json.loads(str(row["new_value_json"])),
                can_retract=all(_can_retract(item, rows, current) for item in group),
                operation_field_count=len(group),
            ))
        return CorrectionList(
            items=items, total=len(rows), revision=len(rows),
        )

    def update(self, event_id: str, payload: EventUpdate) -> dict[str, object]:
        """更新字段并追加逐字段历史；旧客户端未传原因仍记录为未知。

        expected_revision 防止更正历史发生 ABA；expected_values 防止其他入口改值。
        两者可选以兼容旧客户端，新编辑 UI 同时提供。无变化不生成伪更正。
        """
        values = payload.to_column_values()
        with self.repository.edit(event_id) as (connection, current):
            rows = self.repository.rows(connection, event_id)
            revision = len(rows)
            if payload.expected_revision is not None and payload.expected_revision != revision:
                raise CorrectionConflict("活动已被更正，请重新读取后再修改")
            for field, expected in (payload.expected_values or {}).items():
                if field not in {*EDIT_COLUMNS, "schedule"} - {"schedule_json", "updated_at"}:
                    raise ValueError("不支持的预期字段")
                if field_value(current, field) != expected:
                    raise CorrectionConflict("活动内容已变化，请重新读取后再修改")
            if payload.source_item_id and not self.repository.source_belongs(
                connection, event_id, payload.source_item_id,
            ):
                raise ValueError("所选来源不属于此活动")
            if "status" in values and str(values["status"]) != str(current.get("status")):
                # 字段修正入口与状态端点共用同一迁移矩阵，不允许绕道改出非法流转
                current_status = str(current.get("status"))
                allowed = STATUS_TRANSITIONS.get(current_status, set())
                if values["status"] not in allowed:
                    raise EventStatusError(
                        f"不能从 {current_status} 直接变更为 {values['status']}，"
                        f"允许的目标状态：{'、'.join(sorted(allowed)) or '无'}"
                    )
            candidate = {**current, **values}
            self._validate(candidate, values)
            changes = []
            for column in values:
                if column in {"status", "updated_at"}:
                    continue
                field = "schedule" if column == "schedule_json" else column
                old, new = field_value(current, field), field_value(candidate, field)
                if old != new:
                    changes.append((field, old, new, None))
            if any(current.get(column) != value for column, value in values.items()):
                values["updated_at"] = now_iso()
                self.repository.update(connection, event_id, values)
            self._append(
                connection, event_id, changes, payload.correction_reason, payload.source_item_id,
            )
            return {**current, **values}

    def retract(self, event_id: str, operation_id: str, reason: str) -> dict[str, object]:
        """整次撤回，追加反向记录；被后续同字段修改覆盖则 409，不自动覆盖新决定。

        已完成撤回的请求重试只读取当前状态。撤回本身不可再撤回，用户可再次更正。
        合并迁入的历史只读，不能用原活动旧值覆盖保留活动。
        """
        with self.repository.edit(event_id) as (connection, current):
            rows = self.repository.rows(connection, event_id)
            group = [row for row in rows if row["operation_id"] == operation_id]
            if not group:
                raise KeyError(operation_id)
            if all(any(row["reverts_id"] == item["id"] for row in rows) for item in group):
                return current
            if not all(_can_retract(item, rows, current) for item in group):
                raise CorrectionConflict("这次更正已被后续修改或合并覆盖，无法直接撤回")
            patch = {item["field_name"]: json.loads(str(item["old_value_json"])) for item in group}
            payload = EventUpdate.model_validate(patch)
            values = payload.to_column_values()
            candidate = {**current, **values}
            self._validate(candidate, values)
            values["updated_at"] = now_iso()
            self.repository.update(connection, event_id, values)
            changes = [
                (item["field_name"], json.loads(item["new_value_json"]),
                 json.loads(item["old_value_json"]), item["id"])
                for item in group
            ]
            self._append(connection, event_id, changes, reason, None)
            return {**current, **values}

    @staticmethod
    def _validate(candidate: dict, values: dict) -> None:
        if not candidate.get("title") or not str(candidate["title"]).strip():
            raise ValueError("活动标题不能为空")
        if set(values).intersection((*TIME_FIELDS, "schedule_json")):
            schedule = field_value(candidate, "schedule")
            if schedule is not None and not isinstance(schedule, list):
                raise ValueError("已有赛程格式损坏，请先更正赛程")
            validate_event_times({**candidate, "schedule": schedule})

    def _append(self, connection, event_id: str, changes: list, reason, source_id) -> None:
        operation_id, timestamp = new_id("correction"), now_iso()
        for field, old, new, reverts_id in changes:
            self.repository.append(connection, {
                "id": new_id("change"), "operation_id": operation_id,
                "event_id": event_id, "original_event_id": event_id, "field_name": field,
                "old_value_json": json.dumps(old, ensure_ascii=False),
                "new_value_json": json.dumps(new, ensure_ascii=False),
                "actor": "local_user_unverified", "reason": reason, "source_item_id": source_id,
                "action": "retraction" if reverts_id else "correction",
                "reverts_id": reverts_id, "created_at": timestamp,
            })
