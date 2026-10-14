from datetime import UTC, datetime
from decimal import Decimal

import pytest

from reconcile.normalize import normalize_key, parse_amount, parse_currency, parse_datetime


@pytest.mark.parametrize(
    ("raw", "normalizer", "expected"),
    [
        ("SO-0010042", "alnum_upper", "SO10042"),
        ("so10042 ", "alnum_upper", "SO10042"),
        ("#SO-0010042", "digits", "10042"),
        ("  ", "alnum_upper", None),
        (None, "alnum_upper", None),
        ("000", "alnum_upper", "0"),
        ("Order 7", "exact", "Order 7"),
    ],
)
def test_normalize_key(raw: object, normalizer: str, expected: str | None) -> None:
    assert normalize_key(raw, normalizer) == expected


@pytest.mark.parametrize(
    ("raw", "minor", "expected"),
    [
        ("1,204.50", False, Decimal("1204.50")),
        ("$1204.5", False, Decimal("1204.50")),
        ("(12.00)", False, Decimal("-12.00")),
        ("-3", False, Decimal("-3.00")),
        (120450, True, Decimal("1204.50")),
        ("N/A", False, None),
        ("", False, None),
    ],
)
def test_parse_amount(raw: object, minor: bool, expected: Decimal | None) -> None:
    assert parse_amount(raw, minor) == expected


def test_parse_currency() -> None:
    assert parse_currency("usd") == "USD"
    assert parse_currency("US$") is None
    assert parse_currency(None) is None


def test_naive_local_time_is_converted_to_utc() -> None:
    # 23:30 on Sep 1 in Los Angeles (PDT, UTC-7) is 06:30 on Sep 2 UTC.
    got = parse_datetime("2026-09-01 23:30:00", "America/Los_Angeles")
    assert got == datetime(2026, 9, 2, 6, 30, tzinfo=UTC)


def test_epoch_and_iso_z() -> None:
    assert parse_datetime(1788220800) == datetime(2026, 9, 1, tzinfo=UTC)
    assert parse_datetime("2026-09-01T00:00:00Z") == datetime(2026, 9, 1, tzinfo=UTC)
    assert parse_datetime("not a date") is None
