"""End to end: generate a month with planted discrepancies, reconcile it through the CSV
connector, and require the engine to label every single record as planted."""

from collections import Counter

import pytest

from reconcile.connectors import CsvSource
from reconcile.demo import ERP_FIELDS, STRIPE_FIELDS, generate
from reconcile.domain import Bucket, Side
from reconcile.engine import reconcile


@pytest.mark.parametrize("seed", [1, 7, 42, 2026])
def test_engine_recovers_every_planted_discrepancy(tmp_path, seed: int) -> None:  # type: ignore[no-untyped-def]
    data = generate(seed=seed)
    stripe_csv, erp_csv = data.write(tmp_path)
    res = reconcile(
        CsvSource(STRIPE_FIELDS, path=stripe_csv).fetch(Side.LEFT),
        CsvSource(ERP_FIELDS, path=erp_csv).fetch(Side.RIGHT),
    )

    got_left = {o.left.source_id: o.bucket for o in res.outcomes if o.left}
    got_right = {o.right.source_id: o.bucket for o in res.outcomes if o.right}
    wrong_l = {k: (v, got_left.get(k)) for k, v in data.expect_left.items() if got_left.get(k) != v}
    wrong_r = {
        k: (v, got_right.get(k)) for k, v in data.expect_right.items() if got_right.get(k) != v
    }
    assert not wrong_l, wrong_l
    assert not wrong_r, wrong_r
    assert len(got_left) == len(data.stripe)
    assert len(got_right) == len(data.erp)


def test_demo_shape_is_stable() -> None:
    data = generate(seed=7)
    left = Counter(data.expect_left.values())
    right = Counter(data.expect_right.values())
    assert left[Bucket.MISSING_IN_RIGHT] == 6
    assert right[Bucket.MISSING_IN_LEFT] == 5  # 4 never paid + 1 duplicate posting
    assert left[Bucket.UNKNOWN] == 2 and right[Bucket.UNKNOWN] == 3
