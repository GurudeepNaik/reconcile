from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from pathlib import Path

from reconcile.connectors.base import FieldMap, to_record
from reconcile.domain import Record, Side


@dataclass
class CsvSource:
    fields: FieldMap
    path: Path | None = None
    content: bytes | None = None

    def fetch(self, side: Side) -> list[Record]:
        if self.content is not None:
            text = self.content.decode("utf-8-sig")
        elif self.path is not None:
            text = self.path.read_text(encoding="utf-8-sig")
        else:
            raise ValueError("CsvSource needs a path or content")
        return [
            to_record(dict(row), side, self.fields) for row in csv.DictReader(io.StringIO(text))
        ]
