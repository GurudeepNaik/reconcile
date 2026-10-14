from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import create_engine, text

from reconcile.connectors.base import FieldMap, to_record
from reconcile.domain import Record, Side


@dataclass
class SqlSource:
    """Runs one SELECT against any SQLAlchemy URL. Point it at a read-only role."""

    url: str
    query: str
    fields: FieldMap
    params: dict[str, Any] | None = None

    def fetch(self, side: Side) -> list[Record]:
        engine = create_engine(self.url)
        try:
            with engine.connect() as conn:
                rows = conn.execute(text(self.query), self.params or {}).mappings().all()
                conn.rollback()
        finally:
            engine.dispose()
        return [to_record(dict(row), side, self.fields) for row in rows]
