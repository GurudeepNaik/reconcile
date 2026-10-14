"""Normalisation. Most "these don't match" findings are formatting, not data."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

_NON_ALNUM = re.compile(r"[^0-9A-Za-z]")
_LEADING_ZEROS = re.compile(r"^([A-Z]*)0+(\d)")


def key_alnum_upper(value: str) -> str:
    """'so-0010042 ' -> 'SO10042'. Strips punctuation, case and zero padding after a prefix."""
    cleaned = _NON_ALNUM.sub("", value).upper()
    return _LEADING_ZEROS.sub(r"\1\2", cleaned)


def key_digits(value: str) -> str:
    """'#SO-10042' -> '10042'. For systems that only agree on the number."""
    return str(int(digits)) if (digits := re.sub(r"\D", "", value)) else ""


def key_exact(value: str) -> str:
    return value.strip()


KEY_NORMALIZERS: dict[str, Callable[[str], str]] = {
    "alnum_upper": key_alnum_upper,
    "digits": key_digits,
    "exact": key_exact,
}


def normalize_key(value: object, normalizer: str = "alnum_upper") -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return KEY_NORMALIZERS[normalizer](text) or None


def parse_amount(value: object, minor_units: bool = False) -> Decimal | None:
    """Accepts '1,204.50', '$1204.5', 120450 (minor units), Decimal. Returns None if unparseable."""
    if value is None or value == "":
        return None
    if isinstance(value, Decimal):
        amount = value
    else:
        text = str(value).strip().replace(",", "")
        text = re.sub(r"^[^\d\-(]+", "", text)
        negative = text.startswith("(") and text.endswith(")")
        text = text.strip("()")
        try:
            amount = Decimal(text)
        except InvalidOperation:
            return None
        if negative:
            amount = -amount
    if minor_units:
        amount = amount / 100
    return amount.quantize(Decimal("0.01"))


def parse_currency(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip().upper()
    return text if re.fullmatch(r"[A-Z]{3}", text) else None


def parse_datetime(value: object, assume_tz: str = "UTC") -> datetime | None:
    """Naive timestamps are interpreted in `assume_tz` (ERPs often store local time), then
    converted to UTC so both sides compare on the same clock."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, int | float) or (isinstance(value, str) and value.isdigit()):
        dt = datetime.fromtimestamp(int(value), tz=UTC)
    else:
        text = str(value).strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo(assume_tz))
    return dt.astimezone(UTC)
