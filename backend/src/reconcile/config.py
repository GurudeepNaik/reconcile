"""Declarative job specs (YAML) for the CLI. Secrets come from the environment, never the file."""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, Field

from reconcile.connectors import (
    CsvSource,
    FieldMap,
    ShopifyOrdersSource,
    Source,
    SqlSource,
    StripeChargesSource,
)
from reconcile.domain import Rules


class CsvSpec(BaseModel):
    type: Literal["csv"]
    path: Path
    fields: FieldMap


class SqlSpec(BaseModel):
    type: Literal["sql"]
    url_env: str = Field(description="Name of the env var holding the SQLAlchemy URL")
    query: str
    params: dict[str, Any] = {}
    fields: FieldMap


class StripeSpec(BaseModel):
    type: Literal["stripe"]
    api_key_env: str = "STRIPE_API_KEY"
    start: datetime
    end: datetime
    fields: FieldMap | None = None


class ShopifySpec(BaseModel):
    type: Literal["shopify"]
    shop: str
    token_env: str = "SHOPIFY_ACCESS_TOKEN"
    start: datetime
    end: datetime
    fields: FieldMap | None = None


SourceSpec = Annotated[CsvSpec | SqlSpec | StripeSpec | ShopifySpec, Field(discriminator="type")]


class RulesSpec(BaseModel):
    amount_tolerance: Decimal = Decimal("0.00")
    date_window_hours: float = 48
    fuzzy: bool = True
    ambiguity_margin: float = 0.05

    def to_rules(self) -> Rules:
        return Rules(
            amount_tolerance=self.amount_tolerance,
            date_window=timedelta(hours=self.date_window_hours),
            fuzzy=self.fuzzy,
            ambiguity_margin=self.ambiguity_margin,
        )


class JobSpec(BaseModel):
    name: str
    left_label: str = "left"
    right_label: str = "right"
    left: SourceSpec
    right: SourceSpec
    rules: RulesSpec = RulesSpec()


def load_job(path: Path) -> JobSpec:
    data = yaml.safe_load(path.read_text())
    job = JobSpec.model_validate(data)
    for spec in (job.left, job.right):
        if isinstance(spec, CsvSpec) and not spec.path.is_absolute():
            spec.path = (path.parent / spec.path).resolve()
    return job


def _env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"environment variable {name} is not set")
    return value


def build_source(spec: SourceSpec) -> Source:
    match spec:
        case CsvSpec():
            return CsvSource(spec.fields, path=spec.path)
        case SqlSpec():
            return SqlSource(_env(spec.url_env), spec.query, spec.fields, spec.params)
        case StripeSpec():
            src = StripeChargesSource(_env(spec.api_key_env), spec.start, spec.end)
            if spec.fields:
                src.fields = spec.fields
            return src
        case ShopifySpec():
            shop = ShopifyOrdersSource(spec.shop, _env(spec.token_env), spec.start, spec.end)
            if spec.fields:
                shop.fields = spec.fields
            return shop
    raise TypeError(f"unsupported source spec {spec!r}")
