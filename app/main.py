"""Tidy Byte Data Tools API.

A small FastAPI service that wraps our lite Python tools so Rapid can
proxy to a public HTTPS URL. Text PDFs only (no OCR). Files are not stored.
"""

from __future__ import annotations

import csv
import io
import json
import os
import tempfile
from contextlib import contextmanager
from typing import Any, Iterator, Literal, Optional

from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field, field_validator

from app import __version__
from app.auth import check_proxy_secret
from app.flatten import flatten as flatten_json

# Honest limits for a free 512 MB host.
MAX_UPLOAD_BYTES = 8 * 1024 * 1024  # 8 MiB
MAX_TEXT_CHARS = 400_000
MAX_LINKS = 25
MAX_LINK_WORKERS = 5
MAX_LINK_TIMEOUT = 8.0
MAX_JSON_BYTES = 1_000_000

DESCRIPTION = """
Clean up messy data with five small tools: pull tables from a text PDF,
turn a PDF or HTML file into Markdown, check links, split Markdown into
chunks, and shape or flatten JSON.

Honest limits:
- Text PDFs only. Scanned or image-only pages are skipped and listed.
- No OCR and no password-protected PDFs (unless you send the password for Markdown).
- Uploads can be up to 8 MB. Link checks are capped at 25 URLs per call.
- Files are processed in memory and discarded. Nothing is stored.

These tools use the same code as our free lite packages on PyPI.
"""

app = FastAPI(
    title="Tidy Byte Data Tools API",
    description=DESCRIPTION,
    version=__version__,
    contact={
        "name": "Tidy Byte Tools",
        "url": "https://tidybytetools.github.io",
    },
    license_info={"name": "MIT", "url": "https://opensource.org/licenses/MIT"},
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_tags=[
        {"name": "health", "description": "Liveness and readiness. No secret required."},
        {"name": "pdf-tables", "description": "Pull tables out of a text PDF."},
        {"name": "markdown", "description": "Turn a text PDF or HTML into Markdown."},
        {"name": "links", "description": "Check links for status codes and redirects."},
        {"name": "chunk", "description": "Split Markdown into heading-aware chunks."},
        {"name": "json", "description": "Shape messy text into JSON, or flatten nested JSON."},
    ],
)
app.openapi_version = "3.0.3"


@app.exception_handler(HTTPException)
async def http_error(_request: Request, exc: HTTPException) -> JSONResponse:
    detail = exc.detail
    if isinstance(detail, str):
        return JSONResponse({"error": detail}, status_code=exc.status_code)
    return JSONResponse({"error": "request failed", "detail": detail}, status_code=exc.status_code)


def _err(status_code: int, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail=message)


async def _read_upload(file: UploadFile, max_bytes: int | None = None) -> bytes:
    limit = MAX_UPLOAD_BYTES if max_bytes is None else max_bytes
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise _err(
            status.HTTP_413_CONTENT_TOO_LARGE,
            f"File is too big. The limit is {max(1, limit // (1024 * 1024))} MB.",
        )
    if not data:
        raise _err(status.HTTP_400_BAD_REQUEST, "The file was empty.")
    return data


@contextmanager
def _temp_file(data: bytes, suffix: str) -> Iterator[str]:
    fd, path = tempfile.mkstemp(suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        yield path
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _suffix_for(filename: Optional[str], default: str) -> str:
    if not filename or "." not in filename:
        return default
    ext = os.path.splitext(filename)[1].lower()
    return ext if ext else default


def _tables_to_csv_zip_bytes(tables: list[dict[str, Any]]) -> bytes:
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for table in tables:
            name = f"page{table['page']}_table{table['index']}.csv"
            out = io.StringIO()
            writer = csv.writer(out)
            writer.writerows(table["rows"])
            zf.writestr(name, out.getvalue().encode("utf-8-sig"))
    return buf.getvalue()


# ---------- health (open) ----------


@app.get("/health", tags=["health"], summary="Liveness check")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "tidybyte-data-tools", "version": __version__}


@app.get("/ready", tags=["health"], summary="Readiness check")
def ready() -> dict[str, str]:
    return {"status": "ready", "service": "tidybyte-data-tools", "version": __version__}


@app.get("/", tags=["health"], summary="Service info")
def root() -> dict[str, Any]:
    return {
        "name": "Tidy Byte Data Tools API",
        "version": __version__,
        "website": "https://tidybytetools.github.io",
        "docs": "/docs",
        "openapi": "/openapi.json",
        "health": "/health",
    }


# ---------- request models ----------


class LinkCheckBody(BaseModel):
    urls: list[str] = Field(..., min_length=1, max_length=MAX_LINKS)
    timeout: float = Field(5.0, ge=1.0, le=MAX_LINK_TIMEOUT)

    @field_validator("urls")
    @classmethod
    def _trim_urls(cls, urls: list[str]) -> list[str]:
        cleaned = []
        for raw in urls:
            url = (raw or "").strip()
            if not url:
                continue
            if url not in cleaned:
                cleaned.append(url)
        if not cleaned:
            raise ValueError("Please send at least one URL.")
        if len(cleaned) > MAX_LINKS:
            raise ValueError(f"Please send at most {MAX_LINKS} URLs per call.")
        return cleaned


class ChunkBody(BaseModel):
    text: str = Field(..., min_length=1, max_length=MAX_TEXT_CHARS)
    max_chars: int = Field(1500, ge=200, le=20_000)
    overlap_chars: int = Field(0, ge=0, le=5_000)
    prefix_path: bool = False
    source: str = ""


class ShapeBody(BaseModel):
    text: str = Field(..., min_length=1, max_length=MAX_TEXT_CHARS)
    format: Literal["auto", "kv", "csv", "markdown"] = "auto"


class FlattenBody(BaseModel):
    data: Any = None
    text: Optional[str] = None
    separator: str = Field(".", min_length=1, max_length=5)


# ---------- endpoints ----------


@app.post(
    "/v1/pdf-tables",
    tags=["pdf-tables"],
    summary="Pull tables out of a text PDF",
    dependencies=[Depends(check_proxy_secret)],
)
async def pdf_tables(
    file: UploadFile = File(..., description="A text PDF (not a scan)."),
    pages: Optional[str] = Form(None, description="Pages to read, like 1,3-5. Default: all."),
    strategy: Literal["auto", "lines", "text"] = Form("auto"),
    output: Literal["json", "csv"] = Form("json"),
) -> Response:
    from pdf_table_pull_lite import extract

    filename = file.filename or "file.pdf"
    if _suffix_for(filename, ".pdf") != ".pdf":
        raise _err(status.HTTP_400_BAD_REQUEST, "Please upload a .pdf file.")
    data = await _read_upload(file)
    try:
        with _temp_file(data, ".pdf") as path:
            tables, scanned = extract(path, pages, strategy)
    except ValueError as exc:
        raise _err(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except Exception as exc:  # pdfplumber / damaged file
        raise _err(status.HTTP_400_BAD_REQUEST, f"Could not read this PDF: {exc}") from exc

    payload = {
        "ok": True,
        "table_count": len(tables),
        "scanned_pages": scanned,
        "note": (
            "Scanned or image-only pages are skipped. This tool does not do OCR."
            if scanned
            else "Text PDFs only. Scanned pages would be skipped."
        ),
        "tables": [
            {
                "page": t["page"],
                "index": t["index"],
                "rows": t["rows"],
                "row_count": len(t["rows"]),
                "column_count": len(t["rows"][0]) if t["rows"] else 0,
            }
            for t in tables
        ],
    }
    if output == "csv":
        if not tables:
            raise _err(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "No tables found, so there is no CSV to return. Try strategy=text, or use output=json.",
            )
        return Response(
            content=_tables_to_csv_zip_bytes(tables),
            media_type="application/zip",
            headers={"Content-Disposition": "attachment; filename=tables.csv.zip"},
        )
    return JSONResponse(payload)


@app.post(
    "/v1/to-markdown",
    tags=["markdown"],
    summary="Turn a text PDF or HTML file into Markdown",
    dependencies=[Depends(check_proxy_secret)],
)
async def to_markdown(
    file: Optional[UploadFile] = File(None, description="A .pdf, .html, or .htm file."),
    html: Optional[str] = Form(None, description="Raw HTML text, if you are not uploading a file."),
    pages: Optional[str] = Form(None, description="PDF pages to read, like 1,3-5. Default: all."),
    tables: Literal["lines", "text", "off"] = Form("lines"),
    password: Optional[str] = Form(None, description="PDF password, if the file is locked."),
) -> dict[str, Any]:
    from doc2md_lite import blocks_to_md, html_blocks, pdf_blocks

    if file is not None and (file.filename or file.content_type):
        filename = file.filename or "file"
        suffix = _suffix_for(filename, "")
        data = await _read_upload(file)
        if suffix == ".pdf":
            try:
                with _temp_file(data, ".pdf") as path:
                    blocks, scanned = pdf_blocks(path, pages, tables, password)
            except ValueError as exc:
                raise _err(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
            except Exception as exc:
                raise _err(status.HTTP_400_BAD_REQUEST, f"Could not read this PDF: {exc}") from exc
            markdown = blocks_to_md(blocks)
            return {
                "ok": True,
                "type": "pdf",
                "markdown": markdown,
                "chars": len(markdown),
                "tables": sum(1 for b in blocks if b["kind"] == "table"),
                "headings": sum(1 for b in blocks if b["kind"] == "heading"),
                "scanned_pages": scanned,
                "note": (
                    "Scanned or image-only pages are skipped. This tool does not do OCR."
                    if scanned
                    else "Text PDFs only. Scanned pages would be skipped."
                ),
            }
        if suffix in {".html", ".htm", ""}:
            # Empty suffix: treat upload as HTML only if it looks like it.
            text = data.decode("utf-8", errors="replace")
            if suffix == "" and "<" not in text:
                raise _err(status.HTTP_400_BAD_REQUEST, "Please upload a .pdf or .html file.")
            blocks = html_blocks(text, is_text=True)
            markdown = blocks_to_md(blocks)
            return {
                "ok": True,
                "type": "html",
                "markdown": markdown,
                "chars": len(markdown),
                "tables": sum(1 for b in blocks if b["kind"] == "table"),
                "headings": sum(1 for b in blocks if b["kind"] == "heading"),
                "scanned_pages": [],
            }
        raise _err(status.HTTP_400_BAD_REQUEST, "Please upload a .pdf or .html file.")

    if html and html.strip():
        if len(html) > MAX_TEXT_CHARS:
            raise _err(status.HTTP_413_CONTENT_TOO_LARGE, "The HTML text is too long.")
        blocks = html_blocks(html, is_text=True)
        markdown = blocks_to_md(blocks)
        return {
            "ok": True,
            "type": "html",
            "markdown": markdown,
            "chars": len(markdown),
            "tables": sum(1 for b in blocks if b["kind"] == "table"),
            "headings": sum(1 for b in blocks if b["kind"] == "heading"),
            "scanned_pages": [],
        }
    raise _err(status.HTTP_400_BAD_REQUEST, "Please upload a file or send html text.")


@app.post(
    "/v1/link-check",
    tags=["links"],
    summary="Check links for status codes and redirects",
    dependencies=[Depends(check_proxy_secret)],
)
def link_check(body: LinkCheckBody) -> dict[str, Any]:
    from link_check_lite import check_links

    rows = check_links(body.urls, workers=min(MAX_LINK_WORKERS, len(body.urls)), timeout=body.timeout)
    ok_count = sum(1 for r in rows if r.get("ok"))
    return {
        "ok": True,
        "checked": len(rows),
        "ok_count": ok_count,
        "broken_count": len(rows) - ok_count,
        "results": rows,
        "note": f"At most {MAX_LINKS} links per call. Only http and https links are checked.",
    }


@app.post(
    "/v1/chunk-markdown",
    tags=["chunk"],
    summary="Split Markdown into heading-aware chunks",
    dependencies=[Depends(check_proxy_secret)],
)
def chunk_markdown_endpoint(body: ChunkBody) -> dict[str, Any]:
    from md_chunk_lite import chunk_markdown

    try:
        chunks = chunk_markdown(
            body.text,
            max_chars=body.max_chars,
            overlap_chars=body.overlap_chars,
            source=body.source or "text",
            prefix_path=body.prefix_path,
        )
    except ValueError as exc:
        raise _err(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return {"ok": True, "chunk_count": len(chunks), "chunks": chunks}


@app.post(
    "/v1/shape",
    tags=["json"],
    summary="Turn key:value notes, CSV, or a Markdown table into JSON",
    dependencies=[Depends(check_proxy_secret)],
)
def shape_endpoint(body: ShapeBody) -> dict[str, Any]:
    from json_shaper_lite import shape

    try:
        result = shape(body.text, body.format)
    except Exception as exc:
        raise _err(status.HTTP_400_BAD_REQUEST, f"Could not shape this text: {exc}") from exc
    return {"ok": True, "format": result["format"], "data": result["data"]}


@app.post(
    "/v1/flatten-json",
    tags=["json"],
    summary="Flatten nested JSON into dotted keys",
    dependencies=[Depends(check_proxy_secret)],
)
def flatten_endpoint(body: FlattenBody) -> dict[str, Any]:
    if body.data is None and not body.text:
        raise _err(status.HTTP_400_BAD_REQUEST, "Please send JSON in `data`, or a JSON string in `text`.")
    payload: Any = body.data
    if payload is None:
        if len(body.text or "") > MAX_JSON_BYTES:
            raise _err(status.HTTP_413_CONTENT_TOO_LARGE, "The JSON text is too long.")
        try:
            payload = json.loads(body.text or "")
        except json.JSONDecodeError as exc:
            raise _err(status.HTTP_400_BAD_REQUEST, f"The text is not valid JSON: {exc}") from exc
    try:
        flat = flatten_json(payload, sep=body.separator)
    except ValueError as exc:
        raise _err(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return {"ok": True, "keys": len(flat), "separator": body.separator, "data": flat}
