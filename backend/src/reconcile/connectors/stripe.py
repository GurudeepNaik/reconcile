from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import httpx

from reconcile.connectors.base import FieldMap, to_record
from reconcile.domain import Record, Side

DEFAULT_FIELDS = FieldMap(
    id="id",
    key="metadata.order_id",
    amount="amount",
    minor_units=True,
    currency="currency",
    occurred_at="created",
)


@dataclass
class StripeChargesSource:
    """Succeeded charges in [start, end). Uses a restricted, read-only API key."""

    api_key: str
    start: datetime
    end: datetime
    fields: FieldMap = field(default_factory=lambda: DEFAULT_FIELDS)
    base_url: str = "https://api.stripe.com"
    transport: httpx.BaseTransport | None = None

    def fetch(self, side: Side) -> list[Record]:
        params: dict[str, str | int] = {
            "limit": 100,
            "created[gte]": int(self.start.timestamp()),
            "created[lt]": int(self.end.timestamp()),
        }
        records: list[Record] = []
        with httpx.Client(
            base_url=self.base_url,
            auth=(self.api_key, ""),
            timeout=30,
            transport=self.transport,
        ) as client:
            while True:
                resp = client.get("/v1/charges", params=params)
                resp.raise_for_status()
                page = resp.json()
                for charge in page["data"]:
                    if charge.get("status") == "succeeded":
                        records.append(to_record(charge, side, self.fields))
                if not page.get("has_more") or not page["data"]:
                    return records
                params["starting_after"] = page["data"][-1]["id"]
