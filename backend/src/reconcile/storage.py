"""Persistence. A run is identified by a fingerprint of its inputs + rules, so re-submitting the
same files returns the existing run instead of creating a duplicate."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections import Counter
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    create_engine,
    func,
    select,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from reconcile.domain import Bucket, Outcome, Record, Result


class Base(DeclarativeBase):
    pass


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    left_label: Mapped[str] = mapped_column(String(100))
    right_label: Mapped[str] = mapped_column(String(100))
    left_count: Mapped[int] = mapped_column(Integer)
    right_count: Mapped[int] = mapped_column(Integer)
    rules: Mapped[dict[str, Any]] = mapped_column(JSON)
    bucket_counts: Mapped[dict[str, int]] = mapped_column(JSON)
    summaries: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Item(Base):
    __tablename__ = "run_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    bucket: Mapped[str] = mapped_column(String(32), index=True)
    match_type: Mapped[str | None] = mapped_column(String(16))
    reasons: Mapped[list[str]] = mapped_column(JSON)
    confidence: Mapped[float | None] = mapped_column(Float)
    delta: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    left_id: Mapped[str | None] = mapped_column(String(200))
    left_key: Mapped[str | None] = mapped_column(String(200))
    left_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    left_currency: Mapped[str | None] = mapped_column(String(3))
    left_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    right_id: Mapped[str | None] = mapped_column(String(200))
    right_key: Mapped[str | None] = mapped_column(String(200))
    right_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    right_currency: Mapped[str | None] = mapped_column(String(3))
    right_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


def make_engine(url: str) -> Engine:
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    engine = create_engine(url, connect_args=connect_args, pool_pre_ping=True)
    Base.metadata.create_all(engine)
    return engine


def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)


def fingerprint(*parts: bytes | str | dict[str, Any]) -> str:
    h = hashlib.sha256()
    for part in parts:
        if isinstance(part, dict):
            part = json.dumps(part, sort_keys=True, default=str)
        if isinstance(part, str):
            part = part.encode()
        h.update(hashlib.sha256(part).digest())
    return h.hexdigest()


def find_by_fingerprint(session: Session, fp: str) -> Run | None:
    return session.scalar(select(Run).where(Run.fingerprint == fp))


def save_result(
    session: Session,
    *,
    fp: str,
    name: str,
    left_label: str,
    right_label: str,
    rules: dict[str, Any],
    result: Result,
) -> Run:
    counts = Counter(o.bucket.value for o in result.outcomes)
    run = Run(
        id=str(uuid.uuid4()),
        fingerprint=fp,
        name=name,
        left_label=left_label,
        right_label=right_label,
        left_count=result.left_count,
        right_count=result.right_count,
        rules=rules,
        bucket_counts={b.value: counts.get(b.value, 0) for b in Bucket},
        summaries=[
            {
                "currency": s.currency,
                "left_total": str(s.left_total),
                "right_total": str(s.right_total),
                "delta": str(s.delta),
                "explained": {b.value: str(v) for b, v in s.explained.items()},
            }
            for s in result.summaries
        ],
        created_at=datetime.now(UTC),
    )
    session.add(run)
    session.flush()
    session.add_all(_item(run.id, o) for o in result.outcomes)
    session.commit()
    return run


def _item(run_id: str, o: Outcome) -> Item:
    def side(rec: Record | None) -> dict[str, Any]:
        if rec is None:
            return {"id": None, "key": None, "amount": None, "currency": None, "at": None}
        return {
            "id": rec.source_id,
            "key": rec.key,
            "amount": rec.amount,
            "currency": rec.currency,
            "at": rec.occurred_at,
        }

    left, right = side(o.left), side(o.right)
    return Item(
        run_id=run_id,
        bucket=o.bucket.value,
        match_type=o.match_type.value if o.match_type else None,
        reasons=[r.value for r in o.reasons],
        confidence=o.confidence,
        delta=o.delta,
        **{f"left_{k}": v for k, v in left.items()},
        **{f"right_{k}": v for k, v in right.items()},
    )


def list_runs(session: Session, limit: int = 50) -> Sequence[Run]:
    return session.scalars(select(Run).order_by(Run.created_at.desc()).limit(limit)).all()


def get_run(session: Session, run_id: str) -> Run | None:
    return session.get(Run, run_id)


def list_items(
    session: Session, run_id: str, bucket: str | None, limit: int, offset: int
) -> tuple[int, Sequence[Item]]:
    query = select(Item).where(Item.run_id == run_id)
    if bucket:
        query = query.where(Item.bucket == bucket)
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = session.scalars(
        query.order_by(func.abs(Item.delta).desc(), Item.id).limit(limit).offset(offset)
    )
    return total, rows.all()


def iter_items(session: Session, run_id: str) -> Iterator[Item]:
    yield from session.scalars(
        select(Item)
        .where(Item.run_id == run_id)
        .order_by(Item.id)
        .execution_options(yield_per=1000)
    )
