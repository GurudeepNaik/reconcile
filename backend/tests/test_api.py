import csv
import io
import json

import pytest
from fastapi.testclient import TestClient

from reconcile.api import Settings, create_app

FIELDS = json.dumps(
    {"id": "id", "key": "ref", "amount": "amt", "currency": "cur", "occurred_at": "ts"}
)


@pytest.fixture
def client(tmp_path) -> TestClient:  # type: ignore[no-untyped-def]
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'test.db'}", max_upload_mb=1)
    return TestClient(create_app(settings))


def test_demo_run_is_idempotent(client: TestClient) -> None:
    first = client.post("/api/runs/demo")
    assert first.status_code == 201
    again = client.post("/api/runs/demo")
    assert again.status_code == 200
    assert again.json()["id"] == first.json()["id"]
    assert len(client.get("/api/runs").json()) == 1


def test_run_detail_items_and_export(client: TestClient) -> None:
    run = client.post("/api/runs/demo").json()
    counts = run["bucket_counts"]
    assert (
        sum(counts.values())
        == len(client.get(f"/api/runs/{run['id']}/export.csv").text.splitlines()) - 1
    )

    page = client.get(f"/api/runs/{run['id']}/items", params={"bucket": "missing_in_right"}).json()
    assert page["total"] == counts["missing_in_right"]
    assert all(i["bucket"] == "missing_in_right" for i in page["items"])

    usd = next(s for s in run["summaries"] if s["currency"] == "USD")
    assert sum(float(v) for v in usd["explained"].values()) == pytest.approx(float(usd["delta"]))


def upload(client: TestClient, left: str, right: str):  # type: ignore[no-untyped-def]
    return client.post(
        "/api/runs",
        files={"left_file": ("l.csv", left), "right_file": ("r.csv", right)},
        data={"left_fields": FIELDS, "right_fields": FIELDS, "name": "t"},
    )


def test_upload_run(client: TestClient) -> None:
    header = "id,ref,amt,cur,ts\n"
    left = header + "1,A,10.00,USD,2026-09-01T00:00:00Z\n2,B,5.00,USD,2026-09-01T00:00:00Z\n"
    right = header + "9,a,10.00,usd,2026-09-01T01:00:00Z\n"
    resp = upload(client, left, right)
    assert resp.status_code == 201, resp.text
    assert resp.json()["bucket_counts"]["matched"] == 1
    assert resp.json()["bucket_counts"]["missing_in_right"] == 1


def test_export_neutralises_formulas(client: TestClient) -> None:
    header = "id,ref,amt,cur,ts\n"
    run = upload(
        client,
        header + "=HYPERLINK(1),A,1.00,USD,2026-09-01T00:00:00Z\n",
        header + "x,Z,1,USD,2026-01-01\n",
    ).json()
    rows = list(csv.DictReader(io.StringIO(client.get(f"/api/runs/{run['id']}/export.csv").text)))
    assert any(r["left_id"] == "'=HYPERLINK(1)" for r in rows)


def test_rejects_oversized_upload(client: TestClient) -> None:
    big = "id,ref,amt,cur,ts\n" + "x" * (1024 * 1024 + 10)
    assert upload(client, big, big).status_code == 413


def test_rejects_bad_field_map(client: TestClient) -> None:
    resp = client.post(
        "/api/runs",
        files={"left_file": ("l.csv", "a\n"), "right_file": ("r.csv", "a\n")},
        data={"left_fields": "{}", "right_fields": FIELDS},
    )
    assert resp.status_code == 422


def test_unknown_run_404(client: TestClient) -> None:
    assert client.get("/api/runs/nope").status_code == 404
    assert client.get("/api/runs/nope/items").status_code == 404
