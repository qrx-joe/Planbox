from __future__ import annotations

import sqlite3
from contextlib import nullcontext
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from backend.app.core.config import settings
from backend.app.core.temporal import (
    TimeIssue,
    TimeValidationError,
    TimeValueError,
    date_value,
    parse_time,
    precise_epoch,
    time_sort_key,
)
from backend.app.db.database import connect

# 允许按这些字段排序，必须是 events 表真实列名，防止 SQL 注入
SORTABLE_FIELDS = {
    "deadline": "registration_deadline_at",
    "event_start": "event_start_at",
    "created_at": "created_at",
    "updated_at": "updated_at",
    "confidence": "confidence",
    "title": "title",
}

# 默认排序：有截止日期的排前面，按截止日期升序；无截止日期的按创建时间倒序垫底
DEFAULT_ORDER = """
  CASE WHEN registration_deadline_at IS NULL THEN 1 ELSE 0 END,
  registration_deadline_at ASC,
  created_at DESC
"""


def _bound(value: str, field: str) -> date | datetime:
    try:
        return parse_time(value)
    except TimeValueError as exc:
        raise TimeValidationError([TimeIssue(field, exc.code, value)]) from exc


def _day_start(value: date) -> float:
    return datetime.combine(value, time.min, ZoneInfo(settings.timezone)).timestamp()


def _configure_time_functions(connection: sqlite3.Connection) -> None:
    """只给活动查询连接注册纯解析函数；旧值保持不变，无迁移/网络副作用。"""
    connection.create_function("event_time_epoch", 1, precise_epoch, deterministic=True)
    connection.create_function("event_date", 1, date_value, deterministic=True)
    connection.create_function(
        "event_time_sort", 1,
        lambda value: time_sort_key(value, settings.timezone), deterministic=True,
    )


class EventQuery:
    """活动列表查询条件。所有字段可选，未设置的条件不参与过滤。"""

    def __init__(
        self,
        *,
        status: list[str] | None = None,
        keyword: str | None = None,
        event_type: str | None = None,
        is_online: bool | None = None,
        location: str | None = None,
        deadline_before: str | None = None,
        deadline_after: str | None = None,
        event_start_after: str | None = None,
        event_start_before: str | None = None,
        only_actionable: bool = False,
        has_missing_fields: bool | None = None,
        sort: str = "deadline",
        order: str = "asc",
        limit: int | None = None,
        offset: int = 0,
    ) -> None:
        self.status = status
        self.keyword = keyword
        self.event_type = event_type
        self.is_online = is_online
        self.location = location
        self.deadline_before = deadline_before
        self.deadline_after = deadline_after
        self.event_start_after = event_start_after
        self.event_start_before = event_start_before
        self.only_actionable = only_actionable
        self.has_missing_fields = has_missing_fields
        self.sort = sort
        self.order = order
        self.limit = limit
        self.offset = offset
        for after, before in (("deadline_after", "deadline_before"),
                              ("event_start_after", "event_start_before")):
            lower_value, upper_value = getattr(self, after), getattr(self, before)
            lower = _bound(lower_value, after) if lower_value is not None else None
            upper = _bound(upper_value, before) if upper_value is not None else None
            if lower is not None and upper is not None:
                start = lower.timestamp() if isinstance(lower, datetime) else _day_start(lower)
                end = upper.timestamp() if isinstance(upper, datetime) else _day_start(
                    upper + timedelta(days=1)
                )
                if start > end or (start == end and type(upper) is date):
                    raise TimeValidationError([TimeIssue(before, "range_order", upper_value)])

    def _where(self) -> tuple[str, dict[str, object]]:
        clauses: list[str] = []
        params: dict[str, object] = {}

        if self.only_actionable:
            clauses.append("status IN ('interested', 'applied')")
        elif self.status:
            placeholders = []
            for index, value in enumerate(self.status):
                key = f"status_{index}"
                placeholders.append(f":{key}")
                params[key] = value
            clauses.append(f"status IN ({', '.join(placeholders)})")

        if self.keyword:
            clauses.append(
                "(title LIKE :keyword"
                " OR organizer LIKE :keyword"
                " OR location LIKE :keyword"
                " OR event_type LIKE :keyword)"
            )
            params["keyword"] = f"%{self.keyword}%"

        if self.event_type:
            clauses.append("event_type = :event_type")
            params["event_type"] = self.event_type

        if self.is_online is not None:
            clauses.append("is_online = :is_online")
            params["is_online"] = int(self.is_online)

        if self.location:
            clauses.append("location LIKE :location")
            params["location"] = f"%{self.location}%"

        # 精确时刻按实际秒数比较；date 仅按展示日比较，不伪造截止小时。
        for name, column, before in (
            ("deadline_before", "registration_deadline_at", True),
            ("deadline_after", "registration_deadline_at", False),
            ("event_start_before", "event_start_at", True),
            ("event_start_after", "event_start_at", False),
        ):
            value = getattr(self, name)
            if value is None:
                continue
            parsed = _bound(value, name)
            operator = "<=" if before else ">="
            if isinstance(parsed, datetime):
                instant = parsed.timestamp()
                day = parsed.astimezone(ZoneInfo(settings.timezone)).date().isoformat()
            else:
                instant = _day_start(parsed + timedelta(days=1) if before else parsed)
                day = parsed.isoformat()
                if before:
                    operator = "<"
            clauses.append(
                f"(event_time_epoch({column}) {operator} :{name}_instant OR "
                f"event_date({column}) {'<=' if before else '>='} :{name}_day)"
            )
            params[f"{name}_instant"] = instant
            params[f"{name}_day"] = day

        if self.has_missing_fields is True:
            clauses.append("missing_fields IS NOT NULL AND missing_fields NOT IN ('', '[]')")
        elif self.has_missing_fields is False:
            clauses.append("(missing_fields IS NULL OR missing_fields IN ('', '[]'))")

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return where, params

    def _order_by(self) -> str:
        column = SORTABLE_FIELDS.get(self.sort, SORTABLE_FIELDS["deadline"])
        direction = "DESC" if self.order.lower() == "desc" else "ASC"
        if self.sort in ("deadline", "event_start"):
            expression = f"event_time_sort({column})"
            return f"{expression} IS NULL, {expression} {direction}, created_at DESC, id ASC"
        return f"event_time_sort(registration_deadline_at) IS NULL, {column} {direction}, id ASC"


class EventRepository:
    def create(
        self, values: dict[str, object], source_item_id: str,
        connection: sqlite3.Connection | None = None,
    ) -> dict[str, object]:
        """创建活动及来源关联；显式连接由用例提交/回滚，不提前提交或关闭。

        未传连接时保留独立创建并提交的旧行为；返回当前事务可见的活动行。
        """
        columns = ", ".join(values.keys())
        placeholders = ", ".join(f":{key}" for key in values)
        query = f"INSERT INTO events ({columns}) VALUES ({placeholders})"

        with (nullcontext(connection) if connection is not None else connect()) as active:
            active.execute(query, values)
            active.execute(
                """
                INSERT INTO event_sources (event_id, source_item_id, created_at)
                VALUES (?, ?, ?)
                """,
                (values["id"], source_item_id, values["created_at"]),
            )
            if connection is None:
                active.commit()
            row = active.execute(
                "SELECT * FROM events WHERE id = ?",
                (values["id"],),
            ).fetchone()
            if row is None:
                raise RuntimeError("Event was not persisted")
            return dict(row)

    def query(self, query: EventQuery) -> list[dict[str, object]]:
        where, params = query._where()
        sql = f"SELECT * FROM events {where} ORDER BY {query._order_by()}"

        if query.limit is not None:
            sql += " LIMIT :limit"
            params["limit"] = query.limit
            sql += " OFFSET :offset"
            params["offset"] = query.offset
        elif query.offset:
            # SQLite 分页要求先有 LIMIT，用 -1 表示不限量
            sql += " LIMIT -1 OFFSET :offset"
            params["offset"] = query.offset

        with connect() as connection:
            _configure_time_functions(connection)
            rows = connection.execute(sql, params).fetchall()
            return [dict(row) for row in rows]

    def list(self) -> list[dict[str, object]]:
        return self.query(EventQuery())

    def list_actionable(self) -> list[dict[str, object]]:
        return self.query(EventQuery(only_actionable=True))

    def count(self, query: EventQuery) -> int:
        where, params = query._where()
        with connect() as connection:
            _configure_time_functions(connection)
            row = connection.execute(
                f"SELECT COUNT(*) AS total FROM events {where}", params
            ).fetchone()
            return int(row["total"]) if row else 0

    def get(self, event_id: str) -> dict[str, object] | None:
        with connect() as connection:
            row = connection.execute(
                "SELECT * FROM events WHERE id = ?",
                (event_id,),
            ).fetchone()
            return dict(row) if row else None

    def update(self, event_id: str, values: dict[str, object]) -> dict[str, object]:
        if not values:
            return self.get_or_raise(event_id)

        assignments = ", ".join(f"{key} = :{key}" for key in values)
        with connect() as connection:
            connection.execute(
                f"UPDATE events SET {assignments} WHERE id = :event_id",
                {**values, "event_id": event_id},
            )
            connection.commit()
        return self.get_or_raise(event_id)

    def get_or_raise(self, event_id: str) -> dict[str, object]:
        row = self.get(event_id)
        if row is None:
            raise KeyError(event_id)
        return row

    def delete(self, event_id: str) -> None:
        with connect() as connection:
            connection.execute("DELETE FROM evidences WHERE event_id = ?", (event_id,))
            connection.execute(
                "DELETE FROM opportunity_scores WHERE event_id = ?", (event_id,)
            )
            connection.execute("DELETE FROM reminders WHERE event_id = ?", (event_id,))
            connection.execute("DELETE FROM event_sources WHERE event_id = ?", (event_id,))
            connection.execute("DELETE FROM events WHERE id = ?", (event_id,))
            connection.commit()
