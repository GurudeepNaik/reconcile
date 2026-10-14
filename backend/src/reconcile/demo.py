"""Synthetic 'Stripe charges vs ERP orders' month with known, labelled discrepancies.

Each generated row carries the bucket the engine *should* put it in, so the demo doubles as
an end-to-end accuracy test. The ERP stores naive Los Angeles time and pads its order numbers
('SO-0010042'); Stripe stores UTC and the checkout wrote 'so10042' into metadata - the usual
reasons two correct systems look like they disagree.
"""

from __future__ import annotations

import csv
import random
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from reconcile.connectors import FieldMap
from reconcile.domain import Bucket

LA = ZoneInfo("America/Los_Angeles")

STRIPE_FIELDS = FieldMap(
    id="id", key="order_ref", amount="amount", currency="currency", occurred_at="created"
)
ERP_FIELDS = FieldMap(
    id="doc_id",
    key="order_no",
    amount="total",
    currency="curr",
    occurred_at="order_date",
    timezone="America/Los_Angeles",
)


@dataclass
class DemoData:
    stripe: list[dict[str, Any]] = field(default_factory=list)
    erp: list[dict[str, Any]] = field(default_factory=list)
    # source_id -> expected bucket
    expect_left: dict[str, Bucket] = field(default_factory=dict)
    expect_right: dict[str, Bucket] = field(default_factory=dict)

    def write(self, directory: Path) -> tuple[Path, Path]:
        directory.mkdir(parents=True, exist_ok=True)
        paths = directory / "stripe_charges.csv", directory / "erp_orders.csv"
        for path, rows in zip(paths, (self.stripe, self.erp), strict=True):
            with path.open("w", newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
                writer.writeheader()
                writer.writerows(rows)
        return paths


def generate(seed: int = 7, clean_pairs: int = 400) -> DemoData:
    rng = random.Random(seed)
    data = DemoData()
    month_start = datetime(2026, 9, 1, tzinfo=UTC)
    used_amounts: set[Decimal] = set()
    counter = iter(range(10_000, 99_999))

    def amount() -> Decimal:
        while True:
            value = Decimal(rng.randint(1_500, 250_000)) / 100
            if value not in used_amounts:
                used_amounts.add(value)
                return value

    def moment() -> datetime:
        return month_start + timedelta(seconds=rng.randint(0, 29 * 86_400))

    def charge(
        order: int | None, amt: Decimal, at: datetime, expect: Bucket, cur: str = "usd"
    ) -> None:
        cid = f"ch_{rng.getrandbits(48):012x}"
        data.stripe.append(
            {
                "id": cid,
                "order_ref": f"so{order}" if order is not None else "",
                "amount": f"{amt:.2f}",
                "currency": cur,
                "created": at.isoformat().replace("+00:00", "Z"),
            }
        )
        data.expect_left[cid] = expect

    def order(num: int, amt: Decimal | str, at: datetime, expect: Bucket, cur: str = "USD") -> None:
        doc = f"INV-{len(data.erp) + 1:05d}"
        local = at.astimezone(LA).replace(tzinfo=None)
        data.erp.append(
            {
                "doc_id": doc,
                "order_no": f"SO-{num:07d}",
                "total": f"{amt:,.2f}" if isinstance(amt, Decimal) else amt,
                "curr": cur,
                "order_date": local.isoformat(sep=" ", timespec="seconds"),
            }
        )
        data.expect_right[doc] = expect

    # Clean pairs: same order, ERP books it a few minutes later in local time.
    for _ in range(clean_pairs):
        n, a, t = next(counter), amount(), moment()
        charge(n, a, t, Bucket.MATCHED)
        order(n, a, t + timedelta(minutes=rng.randint(0, 90)), Bucket.MATCHED)

    # Late-evening orders: different calendar day in LA vs UTC, still the same order.
    for _ in range(12):
        n, a = next(counter), amount()
        t = month_start + timedelta(days=rng.randint(0, 27), hours=6, minutes=rng.randint(0, 50))
        charge(n, a, t, Bucket.MATCHED)
        order(n, a, t + timedelta(minutes=5), Bucket.MATCHED)

    # Paid in Stripe, never reached the ERP.
    for _ in range(6):
        charge(next(counter), amount(), moment(), Bucket.MISSING_IN_RIGHT)

    # Booked in the ERP, never paid.
    for _ in range(4):
        order(next(counter), amount(), moment(), Bucket.MISSING_IN_LEFT)

    # Amount differs: discount applied in one system only.
    for _ in range(5):
        n, a, t = next(counter), amount(), moment()
        charge(n, a, t, Bucket.MISMATCHED)
        order(n, a + Decimal("12.50"), t, Bucket.MISMATCHED)

    # Currency differs: charged in CAD, booked as USD.
    n, a, t = next(counter), amount(), moment()
    charge(n, a, t, Bucket.MISMATCHED, cur="cad")
    order(n, a, t, Bucket.MISMATCHED)

    # ERP double-posted an order.
    n, a, t = next(counter), amount(), moment()
    charge(n, a, t, Bucket.MATCHED)
    order(n, a, t + timedelta(minutes=2), Bucket.MATCHED)
    order(n, a, t + timedelta(hours=3), Bucket.MISSING_IN_LEFT)

    # Charges whose metadata never got the order id: matched on amount + time.
    for _ in range(8):
        n, a, t = next(counter), amount(), moment()
        charge(None, a, t, Bucket.MATCHED)
        order(n, a, t + timedelta(minutes=rng.randint(1, 30)), Bucket.MATCHED)

    # Two identical keyless charges vs two identical orders: cannot be told apart.
    a, t = amount(), moment()
    charge(None, a, t, Bucket.UNKNOWN)
    charge(None, a, t + timedelta(hours=1), Bucket.UNKNOWN)
    order(next(counter), a, t + timedelta(minutes=10), Bucket.UNKNOWN)
    order(next(counter), a, t + timedelta(minutes=70), Bucket.UNKNOWN)

    # Unparseable ERP row.
    order(next(counter), "N/A", moment(), Bucket.UNKNOWN)

    rng.shuffle(data.stripe)
    rng.shuffle(data.erp)
    return data
