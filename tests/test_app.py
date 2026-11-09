import io
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

FIXTURES = Path("/workspace/products")
RULED_PDF = FIXTURES / "pdf-table-pull-lite/tests/fixtures/ruled.pdf"
HTML_FILE = FIXTURES / "doc2md-lite/tests/fixtures/returns-help.html"
GUIDE_MD = (FIXTURES / "md-chunk-lite/tests/fixtures/guide.md").read_text(encoding="utf-8")

SECRET = "test-proxy-secret-value"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("RAPIDAPI_PROXY_SECRET", SECRET)
    monkeypatch.delenv("RENDER", raising=False)
    monkeypatch.delenv("RENDER_SERVICE_ID", raising=False)
    from app.main import app

    return TestClient(app)


@pytest.fixture
def open_client(monkeypatch):
    monkeypatch.delenv("RAPIDAPI_PROXY_SECRET", raising=False)
    monkeypatch.delenv("RENDER", raising=False)
    monkeypatch.delenv("RENDER_SERVICE_ID", raising=False)
    from app.main import app

    return TestClient(app)


def auth():
    return {"X-RapidAPI-Proxy-Secret": SECRET}


def test_health_is_open(client):
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"
    assert client.get("/ready").status_code == 200
    assert client.get("/").json()["name"].startswith("Tidy Byte")


def test_v1_rejects_missing_or_wrong_secret(client):
    res = client.post("/v1/shape", json={"text": "name: Ada"})
    assert res.status_code == 401
    assert "Proxy-Secret" in res.json()["error"]
    res = client.post(
        "/v1/shape",
        json={"text": "name: Ada"},
        headers={"X-RapidAPI-Proxy-Secret": "nope"},
    )
    assert res.status_code == 401


def test_v1_locked_on_render_without_secret(monkeypatch):
    monkeypatch.delenv("RAPIDAPI_PROXY_SECRET", raising=False)
    monkeypatch.setenv("RENDER", "true")
    from app.main import app

    locked = TestClient(app)
    res = locked.post("/v1/shape", json={"text": "name: Ada"})
    assert res.status_code == 503
    assert locked.get("/health").status_code == 200


def test_shape_kv(client):
    res = client.post("/v1/shape", json={"text": "name: Ada\nrole: guide"}, headers=auth())
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is True
    assert body["format"] == "kv"
    assert body["data"]["name"] == "Ada"


def test_flatten_json(client):
    res = client.post(
        "/v1/flatten-json",
        json={"data": {"user": {"id": 7, "roles": ["admin"]}}},
        headers=auth(),
    )
    assert res.status_code == 200, res.text
    data = res.json()["data"]
    assert data["user.id"] == 7
    assert data["user.roles.0"] == "admin"


def test_chunk_markdown(client):
    res = client.post(
        "/v1/chunk-markdown",
        json={"text": GUIDE_MD, "max_chars": 800},
        headers=auth(),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["chunk_count"] >= 1
    assert "text" in body["chunks"][0]
    assert "heading_path" in body["chunks"][0]


def test_pdf_tables_json(client):
    assert RULED_PDF.is_file()
    with RULED_PDF.open("rb") as fh:
        res = client.post(
            "/v1/pdf-tables",
            headers=auth(),
            files={"file": ("ruled.pdf", fh, "application/pdf")},
            data={"strategy": "lines", "output": "json"},
        )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is True
    assert body["table_count"] >= 1
    assert body["tables"][0]["rows"]


def test_pdf_tables_csv_zip(client):
    with RULED_PDF.open("rb") as fh:
        res = client.post(
            "/v1/pdf-tables",
            headers=auth(),
            files={"file": ("ruled.pdf", fh, "application/pdf")},
            data={"output": "csv"},
        )
    assert res.status_code == 200, res.text
    assert res.headers["content-type"].startswith("application/zip")
    assert res.content[:2] == b"PK"


def test_to_markdown_html_file(client):
    with HTML_FILE.open("rb") as fh:
        res = client.post(
            "/v1/to-markdown",
            headers=auth(),
            files={"file": ("returns-help.html", fh, "text/html")},
        )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["type"] == "html"
    assert len(body["markdown"]) > 20


def test_to_markdown_html_field(client):
    res = client.post(
        "/v1/to-markdown",
        headers=auth(),
        data={"html": "<html><body><h1>Hello</h1><p>World</p></body></html>"},
    )
    assert res.status_code == 200, res.text
    assert "Hello" in res.json()["markdown"]


def test_to_markdown_pdf(client):
    pdf = FIXTURES / "doc2md-lite/tests/fixtures/store-report.pdf"
    with pdf.open("rb") as fh:
        res = client.post(
            "/v1/to-markdown",
            headers=auth(),
            files={"file": ("store-report.pdf", fh, "application/pdf")},
        )
    assert res.status_code == 200, res.text
    assert res.json()["type"] == "pdf"
    assert res.json()["chars"] > 0


def test_upload_too_large(client, monkeypatch):
    monkeypatch.setattr("app.main.MAX_UPLOAD_BYTES", 64)
    res = client.post(
        "/v1/pdf-tables",
        headers=auth(),
        files={"file": ("big.pdf", b"%PDF-" + b"x" * 200, "application/pdf")},
    )
    assert res.status_code == 413


def test_link_check(client):
    res = client.post(
        "/v1/link-check",
        headers=auth(),
        json={"urls": ["https://example.com"], "timeout": 5},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["checked"] == 1
    # example.com should be reachable; if the network is blocked, still return a result row
    assert body["results"][0]["url"] == "https://example.com"


def test_link_check_rejects_too_many(client):
    res = client.post(
        "/v1/link-check",
        headers=auth(),
        json={"urls": [f"https://example.com/{i}" for i in range(26)]},
    )
    assert res.status_code == 422


def test_openapi_available(client):
    res = client.get("/openapi.json")
    assert res.status_code == 200
    spec = res.json()
    assert "Tidy Byte Data Tools API" in spec["info"]["title"]
    paths = spec["paths"]
    for path in (
        "/health",
        "/v1/pdf-tables",
        "/v1/to-markdown",
        "/v1/link-check",
        "/v1/chunk-markdown",
        "/v1/shape",
        "/v1/flatten-json",
    ):
        assert path in paths
