import json
from datetime import UTC, datetime
from decimal import Decimal

import httpx
from sqlalchemy import create_engine, text

from reconcile.connectors import (
    CsvSource,
    FieldMap,
    ShopifyOrdersSource,
    SqlSource,
    StripeChargesSource,
)
from reconcile.domain import Side

START, END = datetime(2026, 9, 1, tzinfo=UTC), datetime(2026, 10, 1, tzinfo=UTC)


def test_stripe_paginates_and_skips_failed_charges() -> None:
    pages = {
        None: {
            "has_more": True,
            "data": [
                {
                    "id": "ch_1",
                    "amount": 1050,
                    "currency": "usd",
                    "created": 1788220800,
                    "status": "succeeded",
                    "metadata": {"order_id": "so1"},
                },
                {
                    "id": "ch_2",
                    "amount": 999,
                    "currency": "usd",
                    "created": 1788220900,
                    "status": "failed",
                    "metadata": {},
                },
            ],
        },
        "ch_2": {
            "has_more": False,
            "data": [
                {
                    "id": "ch_3",
                    "amount": 500,
                    "currency": "jpy",
                    "created": 1788221000,
                    "status": "succeeded",
                    "metadata": {},
                },
            ],
        },
    }
    seen_auth = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_auth.append(request.headers["authorization"])
        assert request.url.params["created[gte]"] == str(int(START.timestamp()))
        return httpx.Response(200, json=pages[request.url.params.get("starting_after")])

    src = StripeChargesSource("rk_test", START, END, transport=httpx.MockTransport(handler))
    got = src.fetch(Side.LEFT)

    assert [r.source_id for r in got] == ["ch_1", "ch_3"]
    assert got[0].amount == Decimal("10.50") and got[0].key == "SO1"
    assert got[1].amount == Decimal("500.00") and got[1].currency == "JPY"  # zero-decimal
    assert all(a.startswith("Basic ") for a in seen_auth)


def test_shopify_follows_cursor_and_drops_cancelled() -> None:
    def node(i: int, cancelled: bool = False) -> dict:  # type: ignore[type-arg]
        return {
            "id": f"gid://shopify/Order/{i}",
            "name": f"#SO{i}",
            "createdAt": "2026-09-02T10:00:00Z",
            "cancelledAt": "2026-09-03T00:00:00Z" if cancelled else None,
            "totalPriceSet": {"shopMoney": {"amount": "12.00", "currencyCode": "USD"}},
        }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-shopify-access-token"] == "shpat_x"
        cursor = json.loads(request.content)["variables"]["cursor"]
        if cursor is None:
            body = {
                "nodes": [node(1), node(2, cancelled=True)],
                "pageInfo": {"hasNextPage": True, "endCursor": "c1"},
            }
        else:
            body = {"nodes": [node(3)], "pageInfo": {"hasNextPage": False, "endCursor": None}}
        return httpx.Response(200, json={"data": {"orders": body}})

    src = ShopifyOrdersSource(
        "x.myshopify.com", "shpat_x", START, END, transport=httpx.MockTransport(handler)
    )
    got = src.fetch(Side.RIGHT)
    assert [r.key for r in got] == ["SO1", "SO3"]


def test_csv_from_bytes_with_bom() -> None:
    content = '﻿id,ref,amt,cur,ts\n1,so-01,"1,000.00",usd,2026-09-01T00:00:00Z\n'.encode()
    fields = FieldMap(id="id", key="ref", amount="amt", currency="cur", occurred_at="ts")
    [r] = CsvSource(fields, content=content).fetch(Side.LEFT)
    assert (r.key, r.amount, r.currency) == ("SO1", Decimal("1000.00"), "USD")


def test_sql_source(tmp_path) -> None:  # type: ignore[no-untyped-def]
    url = f"sqlite:///{tmp_path / 'erp.db'}"
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text("create table orders (doc text, no text, total text, at text)"))
        conn.execute(
            text("insert into orders values ('d1', 'SO-7', '19.99', '2026-09-05 10:00:00')")
        )
    engine.dispose()
    fields = FieldMap(
        id="doc",
        key="no",
        amount="total",
        occurred_at="at",
        default_currency="USD",
        timezone="America/Chicago",
    )
    [r] = SqlSource(url, "select * from orders where total > :min", fields, {"min": 1}).fetch(
        Side.RIGHT
    )
    assert r.key == "SO7" and r.occurred_at == datetime(2026, 9, 5, 15, tzinfo=UTC)
