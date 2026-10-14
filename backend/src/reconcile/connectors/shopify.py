from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx

from reconcile.connectors.base import FieldMap, to_record
from reconcile.domain import Record, Side

QUERY = """
query Orders($cursor: String, $search: String!) {
  orders(first: 250, after: $cursor, query: $search, sortKey: CREATED_AT) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id
      name
      createdAt
      cancelledAt
      totalPriceSet { shopMoney { amount currencyCode } }
    }
  }
}
"""

DEFAULT_FIELDS = FieldMap(
    id="id",
    key="name",
    amount="totalPriceSet.shopMoney.amount",
    currency="totalPriceSet.shopMoney.currencyCode",
    occurred_at="createdAt",
)


@dataclass
class ShopifyOrdersSource:
    """Non-cancelled orders created in [start, end), via the Admin GraphQL API."""

    shop: str
    access_token: str
    start: datetime
    end: datetime
    api_version: str = "2025-07"
    fields: FieldMap = field(default_factory=lambda: DEFAULT_FIELDS)
    transport: httpx.BaseTransport | None = None

    def fetch(self, side: Side) -> list[Record]:
        search = f"created_at:>='{self.start.isoformat()}' created_at:<'{self.end.isoformat()}'"
        variables: dict[str, Any] = {"cursor": None, "search": search}
        records: list[Record] = []
        with httpx.Client(
            base_url=f"https://{self.shop}/admin/api/{self.api_version}",
            headers={"X-Shopify-Access-Token": self.access_token},
            timeout=30,
            transport=self.transport,
        ) as client:
            while True:
                resp = client.post("/graphql.json", json={"query": QUERY, "variables": variables})
                resp.raise_for_status()
                body = resp.json()
                if body.get("errors"):
                    raise RuntimeError(f"Shopify GraphQL error: {body['errors']}")
                orders = body["data"]["orders"]
                records += [
                    to_record(node, side, self.fields)
                    for node in orders["nodes"]
                    if node.get("cancelledAt") is None
                ]
                if not orders["pageInfo"]["hasNextPage"]:
                    return records
                variables["cursor"] = orders["pageInfo"]["endCursor"]
