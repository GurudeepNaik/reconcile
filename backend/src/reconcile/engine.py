"""The matching engine.

Two passes:
  1. Key pass   - records that share a normalised key are paired and compared field by field.
  2. Fuzzy pass - records with no usable key on at least one side are paired on
                  (currency, amount within tolerance, time within window), best score first.

Every input record ends up in exactly one outcome. That is asserted, not hoped for:
a reconciliation that silently drops a row is worse than one that fails loudly.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import defaultdict
from collections.abc import Iterable
from decimal import Decimal

from reconcile.domain import (
    Bucket,
    CurrencySummary,
    MatchType,
    Outcome,
    Reason,
    Record,
    Result,
    Rules,
    Side,
)

UNKNOWN_CURRENCY = "???"


class InvariantError(AssertionError):
    """Raised when the outcomes do not account for every input record exactly once."""


def reconcile(
    left: Iterable[Record], right: Iterable[Record], rules: Rules | None = None
) -> Result:
    rules = rules or Rules()
    lefts, rights = list(left), list(right)
    for r in lefts:
        if r.side is not Side.LEFT:
            raise ValueError(f"record {r.source_id} passed as left but has side={r.side}")
    for r in rights:
        if r.side is not Side.RIGHT:
            raise ValueError(f"record {r.source_id} passed as right but has side={r.side}")

    outcomes: list[Outcome] = []

    valid_l = [r for r in lefts if r.is_valid]
    valid_r = [r for r in rights if r.is_valid]
    outcomes += [_single(r, Bucket.UNKNOWN, Reason.INVALID) for r in lefts if not r.is_valid]
    outcomes += [_single(r, Bucket.UNKNOWN, Reason.INVALID) for r in rights if not r.is_valid]

    key_outcomes, pool_l, pool_r = _key_pass(valid_l, valid_r, rules)
    outcomes += key_outcomes

    if rules.fuzzy:
        fuzzy_outcomes, pool_l, pool_r = _fuzzy_pass(pool_l, pool_r, rules)
        outcomes += fuzzy_outcomes

    outcomes += [_single(r, Bucket.MISSING_IN_RIGHT) for r in pool_l]
    outcomes += [_single(r, Bucket.MISSING_IN_LEFT) for r in pool_r]

    _assert_complete(lefts, rights, outcomes)
    summaries = _summarise(lefts, rights, outcomes)
    return Result(tuple(outcomes), len(lefts), len(rights), summaries)


# --------------------------------------------------------------------------- key pass


def _key_pass(
    lefts: list[Record], rights: list[Record], rules: Rules
) -> tuple[list[Outcome], list[Record], list[Record]]:
    by_key_l: dict[str, list[Record]] = defaultdict(list)
    by_key_r: dict[str, list[Record]] = defaultdict(list)
    pool_l = [r for r in lefts if r.key is None]
    pool_r = [r for r in rights if r.key is None]
    for r in lefts:
        if r.key is not None:
            by_key_l[r.key].append(r)
    for r in rights:
        if r.key is not None:
            by_key_r[r.key].append(r)

    outcomes: list[Outcome] = []
    for key in by_key_l.keys() | by_key_r.keys():
        ls, rs = by_key_l.get(key, []), by_key_r.get(key, [])
        if not ls or not rs:
            # Key exists on one side only; the counterpart may carry no key at all.
            pool_l += ls
            pool_r += rs
            continue
        pairs, extra_l, extra_r = _pair_within_key(ls, rs)
        outcomes += [_compare(a, b, rules) for a, b in pairs]
        # Same key, more rows on one side: a double posting, not a missing record.
        outcomes += [_single(r, Bucket.MISSING_IN_RIGHT, Reason.DUPLICATE_KEY) for r in extra_l]
        outcomes += [_single(r, Bucket.MISSING_IN_LEFT, Reason.DUPLICATE_KEY) for r in extra_r]
    return outcomes, pool_l, pool_r


def _pair_within_key(
    ls: list[Record], rs: list[Record]
) -> tuple[list[tuple[Record, Record]], list[Record], list[Record]]:
    if len(ls) == 1 and len(rs) == 1:
        return [(ls[0], rs[0])], [], []
    candidates = sorted(
        ((_distance(a, b), i, j) for i, a in enumerate(ls) for j, b in enumerate(rs)),
        key=lambda t: t[0],
    )
    used_l: set[int] = set()
    used_r: set[int] = set()
    pairs: list[tuple[Record, Record]] = []
    for _, i, j in candidates:
        if i in used_l or j in used_r:
            continue
        used_l.add(i)
        used_r.add(j)
        pairs.append((ls[i], rs[j]))
    extra_l = [r for i, r in enumerate(ls) if i not in used_l]
    extra_r = [r for j, r in enumerate(rs) if j not in used_r]
    return pairs, extra_l, extra_r


def _distance(a: Record, b: Record) -> tuple[int, Decimal, float]:
    assert a.amount is not None and b.amount is not None
    assert a.occurred_at is not None and b.occurred_at is not None
    return (
        0 if a.currency == b.currency else 1,
        abs(a.amount - b.amount),
        abs((a.occurred_at - b.occurred_at).total_seconds()),
    )


def _compare(a: Record, b: Record, rules: Rules) -> Outcome:
    assert a.amount is not None and b.amount is not None
    assert a.occurred_at is not None and b.occurred_at is not None
    reasons: list[Reason] = []
    if a.currency != b.currency:
        reasons.append(Reason.CURRENCY)
    if abs(a.amount - b.amount) > rules.amount_tolerance:
        reasons.append(Reason.AMOUNT)
    if abs(a.occurred_at - b.occurred_at) > rules.date_window:
        reasons.append(Reason.DATE)
    bucket = Bucket.MISMATCHED if reasons else Bucket.MATCHED
    return Outcome(bucket, a, b, MatchType.KEY, tuple(reasons), 1.0)


# --------------------------------------------------------------------------- fuzzy pass


def _fuzzy_pass(
    lefts: list[Record], rights: list[Record], rules: Rules
) -> tuple[list[Outcome], list[Record], list[Record]]:
    window = rules.date_window.total_seconds()
    tol = rules.amount_tolerance

    # Index the right side by currency, sorted by amount, so each left record only scans
    # the slice within the amount tolerance instead of every right record.
    index: dict[str | None, tuple[list[Decimal], list[int]]] = {}
    for cur in {r.currency for r in rights}:
        js = sorted(
            (j for j, r in enumerate(rights) if r.currency == cur),
            key=lambda j: rights[j].amount or Decimal(0),
        )
        index[cur] = ([rights[j].amount or Decimal(0) for j in js], js)

    candidates: list[tuple[float, int, int]] = []
    by_l: dict[int, list[tuple[float, int]]] = defaultdict(list)
    by_r: dict[int, list[tuple[float, int]]] = defaultdict(list)
    for i, a in enumerate(lefts):
        if a.currency not in index:
            continue
        assert a.amount is not None and a.occurred_at is not None
        amounts, js = index[a.currency]
        lo = bisect_left(amounts, a.amount - tol)
        hi = bisect_right(amounts, a.amount + tol)
        for j in js[lo:hi]:
            b = rights[j]
            # Two records that both carry a key and disagree on it are not the same thing.
            if a.key is not None and b.key is not None:
                continue
            assert b.amount is not None and b.occurred_at is not None
            d_amount = abs(a.amount - b.amount)
            d_time = abs((a.occurred_at - b.occurred_at).total_seconds())
            if d_amount > tol or d_time > window:
                continue
            amount_score = 1.0 if tol == 0 else 1.0 - float(d_amount / tol)
            time_score = 1.0 if window == 0 else 1.0 - d_time / window
            score = round(0.6 * amount_score + 0.4 * time_score, 6)
            candidates.append((score, i, j))
            by_l[i].append((score, j))
            by_r[j].append((score, i))

    candidates.sort(key=lambda t: (-t[0], t[1], t[2]))
    taken_l: set[int] = set()
    taken_r: set[int] = set()
    ambiguous_l: set[int] = set()
    ambiguous_r: set[int] = set()
    outcomes: list[Outcome] = []

    def free_l(i: int) -> bool:
        return i not in taken_l and i not in ambiguous_l

    def free_r(j: int) -> bool:
        return j not in taken_r and j not in ambiguous_r

    for score, i, j in candidates:
        if not (free_l(i) and free_r(j)):
            continue
        floor = score - rules.ambiguity_margin
        rival_r = [j2 for s, j2 in by_l[i] if j2 != j and free_r(j2) and s >= floor]
        rival_l = [i2 for s, i2 in by_r[j] if i2 != i and free_l(i2) and s >= floor]
        if rival_r or rival_l:
            ambiguous_l.update([i, *rival_l])
            ambiguous_r.update([j, *rival_r])
            continue
        taken_l.add(i)
        taken_r.add(j)
        outcomes.append(Outcome(Bucket.MATCHED, lefts[i], rights[j], MatchType.FUZZY, (), score))

    outcomes += [_single(lefts[i], Bucket.UNKNOWN, Reason.AMBIGUOUS) for i in sorted(ambiguous_l)]
    outcomes += [_single(rights[j], Bucket.UNKNOWN, Reason.AMBIGUOUS) for j in sorted(ambiguous_r)]
    rest_l = [r for i, r in enumerate(lefts) if i not in taken_l and i not in ambiguous_l]
    rest_r = [r for j, r in enumerate(rights) if j not in taken_r and j not in ambiguous_r]
    return outcomes, rest_l, rest_r


# --------------------------------------------------------------------------- helpers


def _single(record: Record, bucket: Bucket, reason: Reason | None = None) -> Outcome:
    reasons = (reason,) if reason else ()
    if record.side is Side.LEFT:
        return Outcome(bucket, record, None, None, reasons)
    return Outcome(bucket, None, record, None, reasons)


def _assert_complete(lefts: list[Record], rights: list[Record], outcomes: list[Outcome]) -> None:
    seen_l = [id(o.left) for o in outcomes if o.left is not None]
    seen_r = [id(o.right) for o in outcomes if o.right is not None]
    for side, inputs, seen in (("left", lefts, seen_l), ("right", rights, seen_r)):
        if len(seen) != len(set(seen)):
            raise InvariantError(f"a {side} record was placed in more than one outcome")
        if set(seen) != {id(r) for r in inputs}:
            raise InvariantError(
                f"{side}: {len(inputs)} records in, {len(seen)} accounted for in outcomes"
            )


def _summarise(
    lefts: list[Record], rights: list[Record], outcomes: list[Outcome]
) -> tuple[CurrencySummary, ...]:
    left_tot: dict[str, Decimal] = defaultdict(Decimal)
    right_tot: dict[str, Decimal] = defaultdict(Decimal)
    explained: dict[str, dict[Bucket, Decimal]] = defaultdict(lambda: defaultdict(Decimal))

    for r in lefts:
        if r.amount is not None:
            left_tot[r.currency or UNKNOWN_CURRENCY] += r.amount
    for r in rights:
        if r.amount is not None:
            right_tot[r.currency or UNKNOWN_CURRENCY] += r.amount
    for o in outcomes:
        if o.left and o.left.amount is not None:
            explained[o.left.currency or UNKNOWN_CURRENCY][o.bucket] += o.left.amount
        if o.right and o.right.amount is not None:
            explained[o.right.currency or UNKNOWN_CURRENCY][o.bucket] -= o.right.amount

    summaries = []
    for cur in sorted(left_tot.keys() | right_tot.keys()):
        parts = {b: v for b, v in explained[cur].items() if v != 0}
        summary = CurrencySummary(cur, left_tot[cur], right_tot[cur], parts)
        if sum(parts.values(), Decimal(0)) != summary.delta:
            raise InvariantError(f"{cur}: bucket contributions do not sum to the total delta")
        summaries.append(summary)
    return tuple(summaries)
