"""Core types. Everything the engine touches is immutable and money is always Decimal."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any


class Side(StrEnum):
    LEFT = "left"
    RIGHT = "right"


class Bucket(StrEnum):
    MATCHED = "matched"
    MISMATCHED = "mismatched"
    MISSING_IN_RIGHT = "missing_in_right"
    MISSING_IN_LEFT = "missing_in_left"
    # Never guess. Records we cannot place with confidence land here, with a reason.
    UNKNOWN = "unknown"


class MatchType(StrEnum):
    KEY = "key"
    FUZZY = "fuzzy"


class Reason(StrEnum):
    AMOUNT = "amount_differs"
    CURRENCY = "currency_differs"
    DATE = "date_outside_window"
    DUPLICATE_KEY = "duplicate_key"
    AMBIGUOUS = "ambiguous_candidates"
    INVALID = "invalid_record"


@dataclass(frozen=True, slots=True)
class Record:
    """One row from a source system, already normalised by a connector."""

    side: Side
    source_id: str
    key: str | None
    amount: Decimal | None
    currency: str | None
    occurred_at: datetime | None
    raw: dict[str, Any] = field(default_factory=dict, compare=False, hash=False)

    @property
    def is_valid(self) -> bool:
        return (
            self.amount is not None
            and self.currency is not None
            and self.occurred_at is not None
            and self.occurred_at.tzinfo is not None
        )


@dataclass(frozen=True, slots=True)
class Rules:
    amount_tolerance: Decimal = Decimal("0.00")
    date_window: timedelta = timedelta(hours=48)
    fuzzy: bool = True
    # A fuzzy candidate must beat the runner-up by at least this margin to be accepted.
    ambiguity_margin: float = 0.05


@dataclass(frozen=True, slots=True)
class Outcome:
    bucket: Bucket
    left: Record | None
    right: Record | None
    match_type: MatchType | None = None
    reasons: tuple[Reason, ...] = ()
    confidence: float | None = None

    @property
    def currency(self) -> str | None:
        rec = self.left or self.right
        return rec.currency if rec else None

    @property
    def delta(self) -> Decimal:
        """Contribution of this outcome to (left total - right total)."""
        left = self.left.amount if self.left and self.left.amount is not None else Decimal(0)
        right = self.right.amount if self.right and self.right.amount is not None else Decimal(0)
        return left - right


@dataclass(frozen=True, slots=True)
class CurrencySummary:
    currency: str
    left_total: Decimal
    right_total: Decimal
    explained: dict[Bucket, Decimal]

    @property
    def delta(self) -> Decimal:
        return self.left_total - self.right_total


@dataclass(frozen=True, slots=True)
class Result:
    outcomes: tuple[Outcome, ...]
    left_count: int
    right_count: int
    summaries: tuple[CurrencySummary, ...]

    def by_bucket(self) -> dict[Bucket, list[Outcome]]:
        grouped: dict[Bucket, list[Outcome]] = {b: [] for b in Bucket}
        for o in self.outcomes:
            grouped[o.bucket].append(o)
        return grouped
