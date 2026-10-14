from datetime import UTC, datetime, timedelta
from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from reconcile import Bucket, MatchType, Reason, Record, Rules, Side, reconcile

T0 = datetime(2026, 9, 10, 12, tzinfo=UTC)


def rec(
    side: Side,
    sid: str,
    key: str | None,
    amount: str | None,
    at: datetime | None = T0,
    currency: str | None = "USD",
) -> Record:
    return Record(side, sid, key, Decimal(amount) if amount else None, currency, at)


def L(*args: object, **kw: object) -> Record:
    return rec(Side.LEFT, *args, **kw)  # type: ignore[arg-type]


def R(*args: object, **kw: object) -> Record:
    return rec(Side.RIGHT, *args, **kw)  # type: ignore[arg-type]


def only(result_bucket: list, n: int = 1) -> list:  # type: ignore[type-arg]
    assert len(result_bucket) == n, result_bucket
    return result_bucket


def test_exact_key_match() -> None:
    res = reconcile([L("l1", "A1", "10.00")], [R("r1", "A1", "10.00")])
    [o] = only(res.by_bucket()[Bucket.MATCHED])
    assert o.match_type is MatchType.KEY
    assert res.summaries[0].delta == 0


def test_amount_mismatch_is_reported_with_reason_and_delta() -> None:
    res = reconcile([L("l1", "A1", "10.00")], [R("r1", "A1", "22.50")])
    [o] = only(res.by_bucket()[Bucket.MISMATCHED])
    assert o.reasons == (Reason.AMOUNT,)
    assert o.delta == Decimal("-12.50")
    assert res.summaries[0].explained == {Bucket.MISMATCHED: Decimal("-12.50")}


def test_tolerance_absorbs_rounding() -> None:
    rules = Rules(amount_tolerance=Decimal("0.01"))
    res = reconcile([L("l1", "A1", "10.00")], [R("r1", "A1", "10.01")], rules)
    assert len(res.by_bucket()[Bucket.MATCHED]) == 1
    # Still visible in the money explanation, never silently dropped.
    assert res.summaries[0].explained == {Bucket.MATCHED: Decimal("-0.01")}


def test_date_outside_window() -> None:
    res = reconcile([L("l1", "A1", "10.00")], [R("r1", "A1", "10.00", at=T0 + timedelta(days=5))])
    [o] = only(res.by_bucket()[Bucket.MISMATCHED])
    assert o.reasons == (Reason.DATE,)


def test_currency_mismatch_splits_totals_per_currency() -> None:
    res = reconcile([L("l1", "A1", "10.00", currency="CAD")], [R("r1", "A1", "10.00")])
    [o] = only(res.by_bucket()[Bucket.MISMATCHED])
    assert Reason.CURRENCY in o.reasons
    by_cur = {s.currency: s for s in res.summaries}
    assert by_cur["CAD"].delta == Decimal("10.00")
    assert by_cur["USD"].delta == Decimal("-10.00")


def test_missing_both_directions() -> None:
    res = reconcile([L("l1", "A1", "10.00")], [R("r1", "B2", "99.00")])
    b = res.by_bucket()
    only(b[Bucket.MISSING_IN_RIGHT])
    only(b[Bucket.MISSING_IN_LEFT])


def test_duplicate_key_pairs_closest_and_flags_the_extra() -> None:
    res = reconcile(
        [L("l1", "A1", "10.00")],
        [R("r1", "A1", "10.00", at=T0 + timedelta(hours=3)), R("r2", "A1", "10.00")],
    )
    b = res.by_bucket()
    [m] = only(b[Bucket.MATCHED])
    assert m.right is not None and m.right.source_id == "r2"
    [extra] = only(b[Bucket.MISSING_IN_LEFT])
    assert extra.reasons == (Reason.DUPLICATE_KEY,)


def test_fuzzy_match_when_one_side_has_no_key() -> None:
    res = reconcile(
        [L("l1", None, "57.30")], [R("r1", "SO1", "57.30", at=T0 + timedelta(minutes=20))]
    )
    [o] = only(res.by_bucket()[Bucket.MATCHED])
    assert o.match_type is MatchType.FUZZY
    assert o.confidence is not None and 0.9 < o.confidence <= 1


def test_records_with_conflicting_keys_are_never_fuzzy_matched() -> None:
    res = reconcile([L("l1", "A1", "57.30")], [R("r1", "B2", "57.30")])
    assert not res.by_bucket()[Bucket.MATCHED]


def test_fuzzy_can_be_disabled() -> None:
    res = reconcile([L("l1", None, "5.00")], [R("r1", "X", "5.00")], Rules(fuzzy=False))
    assert not res.by_bucket()[Bucket.MATCHED]


def test_indistinguishable_candidates_go_to_unknown_not_a_guess() -> None:
    res = reconcile(
        [L("l1", None, "49.00"), L("l2", None, "49.00", at=T0 + timedelta(hours=1))],
        [
            R("r1", "SO1", "49.00", at=T0 + timedelta(minutes=10)),
            R("r2", "SO2", "49.00", at=T0 + timedelta(minutes=70)),
        ],
    )
    unknown = res.by_bucket()[Bucket.UNKNOWN]
    assert len(unknown) == 4
    assert all(o.reasons == (Reason.AMBIGUOUS,) for o in unknown)


def test_clear_winner_is_not_ambiguous() -> None:
    res = reconcile(
        [L("l1", None, "49.00")],
        [
            R("r1", "SO1", "49.00", at=T0 + timedelta(minutes=5)),
            R("r2", "SO2", "49.00", at=T0 + timedelta(hours=40)),
        ],
    )
    [m] = only(res.by_bucket()[Bucket.MATCHED])
    assert m.right is not None and m.right.source_id == "r1"
    only(res.by_bucket()[Bucket.MISSING_IN_LEFT])


def test_invalid_records_are_unknown_and_still_counted() -> None:
    res = reconcile([L("l1", "A1", None)], [R("r1", "A1", "10.00", at=None)])
    unknown = res.by_bucket()[Bucket.UNKNOWN]
    assert len(unknown) == 2
    assert all(o.reasons == (Reason.INVALID,) for o in unknown)


# --------------------------------------------------------------------- properties

amounts = st.sampled_from(["5.00", "5.01", "10.00", "49.00", "120.00", "999.99"])
keys = st.one_of(st.none(), st.sampled_from(["A", "B", "C", "D"]))
offsets = st.integers(min_value=0, max_value=96 * 3600)
currencies = st.sampled_from(["USD", "USD", "USD", "EUR"])


@st.composite
def records(draw: st.DrawFn, side: Side) -> list[Record]:
    n = draw(st.integers(min_value=0, max_value=25))
    out = []
    for i in range(n):
        amount = (
            draw(st.one_of(st.none(), amounts)) if draw(st.integers(0, 20)) == 0 else draw(amounts)
        )
        out.append(
            Record(
                side,
                f"{side.value}{i}",
                draw(keys),
                Decimal(amount) if amount else None,
                draw(currencies),
                T0 + timedelta(seconds=draw(offsets)),
            )
        )
    return out


@settings(max_examples=400, deadline=None)
@given(records(Side.LEFT), records(Side.RIGHT), st.sampled_from(["0.00", "0.01", "1.00"]))
def test_every_record_is_accounted_for_and_money_explained(
    lefts: list[Record], rights: list[Record], tol: str
) -> None:
    # reconcile() raises InvariantError internally if either property fails;
    # re-check here so the test states the contract explicitly.
    res = reconcile(lefts, rights, Rules(amount_tolerance=Decimal(tol)))
    placed_l = [o.left for o in res.outcomes if o.left]
    placed_r = [o.right for o in res.outcomes if o.right]
    assert sorted(r.source_id for r in placed_l) == sorted(r.source_id for r in lefts)
    assert sorted(r.source_id for r in placed_r) == sorted(r.source_id for r in rights)
    for s in res.summaries:
        assert sum(s.explained.values(), Decimal(0)) == s.left_total - s.right_total
    for o in res.outcomes:
        if o.bucket is Bucket.MATCHED:
            assert o.left and o.right and o.left.amount is not None and o.right.amount is not None
            assert abs(o.left.amount - o.right.amount) <= Decimal(tol)
