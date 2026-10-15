from __future__ import annotations

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

        # 区间语义：before/after 只约束registration_deadline_at 本身，
        # 与 only_actionable 是「与」关系 —— 可筛「interested 且 7 天内截止」
        deadline_column = "registration_deadline_at IS NOT NULL AND registration_deadline_at"

        if self.deadline_before:
            clauses.append(f"{deadline_column} <= :deadline_before")
            params["deadline_before"] = self.deadline_before

        if self.deadline_after:
            clauses.append(f"{deadline_column} >= :deadline_after")
            params["deadline_after"] = self.deadline_after

        if self.event_start_after:
            clauses.append("event_start_at IS NOT NULL AND event_start_at >= :event_start_after")
            params["event_start_after"] = self.event_start_after

        if self.event_start_before:
            clauses.append("event_start_at IS NOT NULL AND event_start_at <= :event_start_before")
            params["event_start_before"] = self.event_start_before

        if self.has_missing_fields is True:
            clauses.append("missing_fields IS NOT NULL AND missing_fields NOT IN ('', '[]')")
        elif self.has_missing_fields is False:
            clauses.append("(missing_fields IS NULL OR missing_fields IN ('', '[]'))")

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return where, params

    def _order_by(self) -> str:
        column = SORTABLE_FIELDS.get(self.sort, SORTABLE_FIELDS["deadline"])
        direction = "DESC" if self.order.lower() == "desc" else "ASC"
        # 有截止日期的始终优先，非目标排序字段时才叠加这个保护逻辑
        return f"CASE WHEN registration_deadline_at IS NULL THEN 1 ELSE 0 END, {column} {direction}"


class EventRepository:
    def create(self, values: dict[str, object], source_item_id: str) -> dict[str, object]:
        columns = ", ".join(values.keys())
        placeholders = ", ".join(f":{key}" for key in values)
        query = f"INSERT INTO events ({columns}) VALUES ({placeholders})"

        with connect() as connection:
            connection.execute(query, values)
            connection.execute(
                """
                INSERT INTO event_sources (event_id, source_item_id, created_at)
                VALUES (?, ?, ?)
                """,
                (values["id"], source_item_id, values["created_at"]),
            )
            connection.commit()
            row = connection.execute(
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
            rows = connection.execute(sql, params).fetchall()
            return [dict(row) for row in rows]

    def list(self) -> list[dict[str, object]]:
        return self.query(EventQuery())

    def list_actionable(self) -> list[dict[str, object]]:
        return self.query(EventQuery(only_actionable=True))

    def count(self, query: EventQuery) -> int:
        where, params = query._where()
        with connect() as connection:
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