"""A connector turns rows from a system into Records via a declarative field map."""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel

from reconcile.domain import Record, Side
from reconcile.normalize import normalize_key, parse_amount, parse_currency, parse_datetime


class FieldMap(BaseModel):
    """Where each field lives in a source row. Dotted paths reach into nested JSON."""

    id: str
    amount: str
    occurred_at: str
    key: str | None = None
    currency: str | None = None
    default_currency: str | None = None
    timezone: str = "UTC"
    minor_units: bool = False
    key_normalizer: str = "alnum_upper"


class Source(Protocol):
    def fetch(self, side: Side) -> list[Record]: ...


def get_path(row: dict[str, Any], path: str) -> Any:
    value: Any = row
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


# Currencies Stripe (and most processors) send without minor units.
ZERO_DECIMAL = frozenset(
    [
        "BIF",
        "CLP",
        "DJF",
        "GNF",
        "JPY",
        "KMF",
        "KRW",
        "MGA",
        "PYG",
        "RWF",
        "UGX",
        "VND",
        "VUV",
        "XAF",
        "XOF",
        "XPF",
    ]
)


def to_record(row: dict[str, Any], side: Side, fields: FieldMap) -> Record:
    currency = parse_currency(get_path(row, fields.currency)) if fields.currency else None
    currency = currency or parse_currency(fields.default_currency)
    minor_units = fields.minor_units and currency not in ZERO_DECIMAL
    return Record(
        side=side,
        source_id=str(get_path(row, fields.id)),
        key=normalize_key(get_path(row, fields.key), fields.key_normalizer) if fields.key else None,
        amount=parse_amount(get_path(row, fields.amount), minor_units),
        currency=currency,
        occurred_at=parse_datetime(get_path(row, fields.occurred_at), fields.timezone),
        raw=row,
    )
