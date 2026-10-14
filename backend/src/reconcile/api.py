import csv
import io
from collections.abc import Iterator
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Response, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from reconcile import demo, storage
from reconcile.config import RulesSpec
from reconcile.connectors import CsvSource, FieldMap
from reconcile.domain import Bucket, Side
from reconcile.engine import reconcile


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RECONCILE_")

    database_url: str = "sqlite:///./reconcile.db"
    max_upload_mb: int = 20
    cors_origins: list[str] = ["http://localhost:5173"]


# ----------------------------------------------------------------------------- schemas


class RunOut(BaseModel):
    id: str
    name: str
    left_label: str
    right_label: str
    left_count: int
    right_count: int
    rules: dict[str, Any]
    bucket_counts: dict[str, int]
    summaries: list[dict[str, Any]]
    created_at: datetime


class ItemOut(BaseModel):
    bucket: str
    match_type: str | None
    reasons: list[str]
    confidence: float | None
    delta: Decimal
    left_id: str | None
    left_key: str | None
    left_amount: Decimal | None
    left_currency: str | None
    left_at: datetime | None
    right_id: str | None
    right_key: str | None
    right_amount: Decimal | None
    right_currency: str | None
    right_at: datetime | None


class ItemPage(BaseModel):
    total: int
    items: list[ItemOut]


# ----------------------------------------------------------------------------- app


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    engine = storage.make_engine(settings.database_url)
    make_session = storage.session_factory(engine)
    max_bytes = settings.max_upload_mb * 1024 * 1024

    app = FastAPI(title="reconcile", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    def get_session() -> Iterator[Session]:
        with make_session() as session:
            yield session

    SessionDep = Annotated[Session, Depends(get_session)]

    def persist(
        session: Session,
        response: Response,
        *,
        fp: str,
        name: str,
        labels: tuple[str, str],
        rules: RulesSpec,
        left: CsvSource,
        right: CsvSource,
    ) -> RunOut:
        if existing := storage.find_by_fingerprint(session, fp):
            response.status_code = status.HTTP_200_OK
            return RunOut.model_validate(existing, from_attributes=True)
        result = reconcile(left.fetch(Side.LEFT), right.fetch(Side.RIGHT), rules.to_rules())
        try:
            run = storage.save_result(
                session,
                fp=fp,
                name=name,
                left_label=labels[0],
                right_label=labels[1],
                rules=rules.model_dump(mode="json"),
                result=result,
            )
        except IntegrityError:
            # A concurrent request with identical inputs won the race; return its run.
            session.rollback()
            winner = storage.find_by_fingerprint(session, fp)
            if winner is None:
                raise
            response.status_code = status.HTTP_200_OK
            return RunOut.model_validate(winner, from_attributes=True)
        return RunOut.model_validate(run, from_attributes=True)

    async def read_capped(upload: UploadFile) -> bytes:
        data = await upload.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise HTTPException(
                status.HTTP_413_CONTENT_TOO_LARGE,
                f"{upload.filename} exceeds {settings.max_upload_mb} MB",
            )
        return data

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/runs", status_code=status.HTTP_201_CREATED)
    async def create_run(
        session: SessionDep,
        response: Response,
        left_file: Annotated[UploadFile, File()],
        right_file: Annotated[UploadFile, File()],
        left_fields: Annotated[str, Form(description="FieldMap JSON")],
        right_fields: Annotated[str, Form(description="FieldMap JSON")],
        name: Annotated[str, Form(max_length=200)] = "Untitled run",
        left_label: Annotated[str, Form(max_length=100)] = "left",
        right_label: Annotated[str, Form(max_length=100)] = "right",
        rules: Annotated[str, Form(description="RulesSpec JSON")] = "{}",
    ) -> RunOut:
        try:
            lf = FieldMap.model_validate_json(left_fields)
            rf = FieldMap.model_validate_json(right_fields)
            rs = RulesSpec.model_validate_json(rules)
        except ValidationError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, exc.errors()) from exc
        left_bytes, right_bytes = await read_capped(left_file), await read_capped(right_file)
        fp = storage.fingerprint(
            left_bytes,
            right_bytes,
            lf.model_dump(),
            rf.model_dump(),
            rs.model_dump(mode="json"),
        )
        try:
            return persist(
                session,
                response,
                fp=fp,
                name=name,
                labels=(left_label, right_label),
                rules=rs,
                left=CsvSource(lf, content=left_bytes),
                right=CsvSource(rf, content=right_bytes),
            )
        except UnicodeDecodeError as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "files must be UTF-8 CSV"
            ) from exc

    @app.post("/api/runs/demo", status_code=status.HTTP_201_CREATED)
    def create_demo_run(session: SessionDep, response: Response, seed: int = 7) -> RunOut:
        data = demo.generate(seed=seed)
        left_bytes, right_bytes = _csv_bytes(data.stripe), _csv_bytes(data.erp)
        rs = RulesSpec()
        fp = storage.fingerprint(
            left_bytes,
            right_bytes,
            demo.STRIPE_FIELDS.model_dump(),
            demo.ERP_FIELDS.model_dump(),
            rs.model_dump(mode="json"),
        )
        return persist(
            session,
            response,
            fp=fp,
            name=f"Demo: Stripe vs ERP, September 2026 (seed {seed})",
            labels=("Stripe", "ERP"),
            rules=rs,
            left=CsvSource(demo.STRIPE_FIELDS, content=left_bytes),
            right=CsvSource(demo.ERP_FIELDS, content=right_bytes),
        )

    @app.get("/api/runs")
    def runs(session: SessionDep) -> list[RunOut]:
        return [RunOut.model_validate(r, from_attributes=True) for r in storage.list_runs(session)]

    @app.get("/api/runs/{run_id}")
    def run(run_id: str, session: SessionDep) -> RunOut:
        found = storage.get_run(session, run_id)
        if found is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found")
        return RunOut.model_validate(found, from_attributes=True)

    @app.get("/api/runs/{run_id}/items")
    def items(
        run_id: str,
        session: SessionDep,
        bucket: Bucket | None = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> ItemPage:
        if storage.get_run(session, run_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found")
        total, rows = storage.list_items(
            session, run_id, bucket.value if bucket else None, limit, offset
        )
        return ItemPage(
            total=total, items=[ItemOut.model_validate(r, from_attributes=True) for r in rows]
        )

    @app.get("/api/runs/{run_id}/export.csv")
    def export(run_id: str) -> StreamingResponse:
        with make_session() as session:
            if storage.get_run(session, run_id) is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found")

        def rows() -> Iterator[str]:
            cols = list(ItemOut.model_fields)
            buf = io.StringIO()
            writer = csv.writer(buf)
            writer.writerow(cols)
            with make_session() as session:
                for item in storage.iter_items(session, run_id):
                    out = ItemOut.model_validate(item, from_attributes=True).model_dump(mode="json")
                    writer.writerow([_cell(c, out[c]) for c in cols])
                    yield buf.getvalue()
                    buf.seek(0)
                    buf.truncate()
            yield buf.getvalue()

        headers = {"Content-Disposition": f'attachment; filename="reconcile-{run_id}.csv"'}
        return StreamingResponse(rows(), media_type="text/csv", headers=headers)

    return app


def _csv_bytes(rows: list[dict[str, Any]]) -> bytes:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue().encode()


_TEXT_COLUMNS = {"left_id", "left_key", "right_id", "right_key"}


def _cell(column: str, value: Any) -> Any:
    """Neutralise spreadsheet formula injection in identifiers that came from source data."""
    if column == "reasons":
        return ";".join(value)
    if column in _TEXT_COLUMNS and isinstance(value, str) and value[:1] in ("=", "+", "-", "@"):
        return "'" + value
    return value
