"""Vercel entry point: the FastAPI app as one Python function serving /api/*.

Serverless instances share no disk, so the demo needs a real database: connect a Neon (Postgres)
database to the Vercel project and it provides DATABASE_URL.
"""

import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode

from fastapi.staticfiles import StaticFiles

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend" / "src"))

from reconcile.api import Settings, create_app  # noqa: E402


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL") or ""
    if not url:
        raise RuntimeError("DATABASE_URL is not set: connect a Neon database to this project")
    # Neon hands out postgres:// URLs; SQLAlchemy needs the driver named explicitly.
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix) :]
    return url


def _restore_path(inner: Any) -> Any:
    """vercel.json rewrites /api/<path> to this function as /api/index?__path=<path>. Put the
    original path back before FastAPI routes the request (a no-op if Vercel already kept it)."""

    async def asgi(scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] == "http" and scope["path"].rstrip("/") in ("/api/index", "/api/index.py"):
            query = parse_qsl(scope.get("query_string", b"").decode(), keep_blank_values=True)
            original = [v for k, v in query if k == "__path"]
            if original:
                path = "/api/" + original[0].lstrip("/")
                rest = urlencode([(k, v) for k, v in query if k != "__path"])
                scope = {**scope, "path": path, "raw_path": path.encode(), "query_string": rest.encode()}
        await inner(scope, receive, send)

    return asgi


# Vercel caps request bodies at 4.5 MB, so cap uploads below that. Same origin: no CORS needed.
_api = create_app(Settings(database_url=_database_url(), max_upload_mb=4, cors_origins=[]))

# The dashboard is built into public/ (see vercel.json) and served by the same app; the /api
# routes above take precedence over this catch-all mount.
_dashboard = Path(__file__).resolve().parent.parent / "public"
if _dashboard.is_dir():
    _api.mount("/", StaticFiles(directory=_dashboard, html=True), name="dashboard")

app = _restore_path(_api)
