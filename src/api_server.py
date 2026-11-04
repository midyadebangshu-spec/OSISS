"""HTTP API server for OSISS search endpoints."""

from __future__ import annotations

import os
import re
import sys
import threading
import time
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from starlette.concurrency import run_in_threadpool

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)

import accounts_api
import admin
import admin_api
import concept_api
import layout
import library_api
import load_control
import page_assets
import page_labels
import replica
import runtime_config
import workspace_api
from config import settings
from db_pool import db
from search import search_and_extract


# QA answers scoring below this are near-random spans; the UI should not highlight them as "the answer".
ANSWER_MIN_SCORE = 0.02

PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
FRONTEND_DIST = os.path.join(PROJECT_ROOT, "frontend", "dist")
os.makedirs(DATA_DIR, exist_ok=True)

# Paths the search-access rule (open / key) and CORS apply to. Admin routes have their own token auth.
PUBLIC_PREFIXES = ("/api/search", "/api/concepts", "/api/library", "/data/")


def ensure_schema(wait_seconds: float = 0) -> bool:
    """Create tables added after the first setup; idempotent. Retries for up to `wait_seconds` (the database may
    still be starting when several containers come up together). Returns whether it succeeded."""
    from db_init import POSTGRES_SCHEMA_SQL  # pylint: disable=import-outside-toplevel

    deadline = time.monotonic() + wait_seconds
    while True:
        try:
            with db() as connection:
                with connection:
                    with connection.cursor() as cursor:
                        cursor.execute(POSTGRES_SCHEMA_SQL)
            return True
        except Exception as exc:  # pylint: disable=broad-except
            if time.monotonic() >= deadline:
                print(f"[OSISS] Could not update the PostgreSQL schema yet: {exc}")
                return False
            time.sleep(2)


def start_admin_worker() -> None:
    """Start the in-process ingestion worker the admin app controls (only when the admin API is enabled)."""
    if not settings.admin_token:
        print("[OSISS] ADMIN_TOKEN is not set; admin API and ingestion worker are disabled.")
        return
    import db_pool  # pylint: disable=import-outside-toplevel
    import search  # pylint: disable=import-outside-toplevel
    from ingest_worker import IngestWorker  # pylint: disable=import-outside-toplevel

    worker = IngestWorker(
        connect=db_pool.connect,
        es_client=search.get_es_client,
        embedder=lambda: search.get_models()[0],
        on_corpus_change=load_control.corpus_changed,
    )
    # Every copy can queue and show jobs; only copies with INGEST_WORKER=true run them.
    worker.start(polling=bool(runtime_config.get_state("ingester_polling", False)), run_worker=settings.ingest_worker)
    admin_api.context.worker = worker


@asynccontextmanager
async def lifespan(_app: FastAPI):
    stop = threading.Event()
    if settings.state_backend == "postgres":
        # Settings, jobs and lockouts live in PostgreSQL in this mode, so the tables must exist first.
        if not ensure_schema(wait_seconds=120):
            raise RuntimeError("STATE_BACKEND=postgres but PostgreSQL is unreachable; not starting.")
    else:
        threading.Thread(target=ensure_schema, name="osiss-schema", daemon=True).start()
    for warning in runtime_config.load_overrides():
        print(f"[OSISS] {warning}")
    for note in load_control.configure():
        print(f"[OSISS] {note}")
    admin_api.configure()
    runtime_config.start_sync(stop)
    replica.start_warm_up()
    start_admin_worker()
    replica.start_heartbeat(stop)
    yield
    stop.set()
    if admin_api.context.worker is not None:
        admin_api.context.worker.shutdown()


app = FastAPI(title="OSISS API", version=admin_api.VERSION, lifespan=lifespan)

admin_api.context.es_client = lambda: __import__("search").get_es_client()
admin_api.context.search = lambda query, top_k, timings: search_and_extract(query, top_k, timings=timings)
app.include_router(admin_api.router)
app.include_router(concept_api.router)
app.include_router(library_api.router)
app.include_router(accounts_api.router)
app.include_router(workspace_api.router)


def same_site(request: Request) -> bool:
    """Request made by a page served from this server (or from PUBLIC_BASE_URL)."""
    if request.headers.get("sec-fetch-site") == "same-origin":
        return True
    hosts = {request.headers.get("host", "").lower()}
    if settings.public_base_url:
        hosts.add(urlparse(settings.public_base_url).netloc.lower())
    for header in ("origin", "referer"):
        value = request.headers.get(header)
        if value and urlparse(value).netloc.lower() in hosts:
            return True
    return False


def from_allowed_origin(request: Request) -> bool:
    allowed = set(runtime_config.cors_origin_list())
    if not allowed:
        return False
    origin = (request.headers.get("origin") or "").rstrip("/").lower()
    if origin in allowed:
        return True
    referer = urlparse(request.headers.get("referer") or "")
    return bool(referer.netloc) and f"{referer.scheme}://{referer.netloc}".lower() in allowed


def check_search_access(request: Request) -> Tuple[Optional[Response], Optional[int]]:
    """Returns (refusal or None, API client id or None).

    In "key" mode a request needs a valid API key or must come from this site / an allowed origin. Browsers set
    Origin, Referer and Sec-Fetch-Site honestly, so this keeps other websites out; a script can forge them, which
    is why the website exemption is a light lock and the API key is the real one. In "open" mode a key is optional,
    but a valid one is still counted (usage in the admin app) and gets the per-key rate limit.
    """
    key_mode = settings.search_access == "key"
    key = request.headers.get("x-api-key") or (
        request.query_params.get("key") if key_mode and request.url.path.startswith("/data/") else None
    )
    if key:
        try:
            with db() as connection:
                client_id = admin.use_api_key(connection, key)
        except Exception:  # pylint: disable=broad-except
            if not key_mode:
                return None, None
            return JSONResponse({"detail": "API key check is unavailable (database down)."}, status_code=503), None
        if client_id is not None:
            return None, client_id
        if key_mode:
            return JSONResponse({"detail": "Invalid or disabled API key."}, status_code=401), None
        return None, None
    if not key_mode or same_site(request) or from_allowed_origin(request):
        return None, None
    return JSONResponse({"detail": "An API key is required (X-API-Key header)."}, status_code=401), None


def cors_headers(request: Request) -> Dict[str, str]:
    origin = (request.headers.get("origin") or "").rstrip("/")
    if not origin or origin.lower() not in set(runtime_config.cors_origin_list()):
        return {}
    return {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, X-API-Key",
        "Access-Control-Max-Age": "600",
        "Vary": "Origin",
    }


@app.middleware("http")
async def public_access(request: Request, call_next):
    """CORS for allowed origins and the open/key access rule on search and PDF routes."""
    path = request.url.path
    if not (path.startswith(PUBLIC_PREFIXES) or path == "/health"):
        return await call_next(request)
    headers = cors_headers(request)
    if request.method == "OPTIONS" and request.headers.get("access-control-request-method"):
        return Response(status_code=204 if headers else 403, headers=headers)
    denied, client_id = None, None
    if path != "/health":
        # The API-key check queries PostgreSQL; keep that off the event loop.
        denied, client_id = await run_in_threadpool(check_search_access, request)
    request.state.api_client_id = client_id
    response = denied or await call_next(request)
    response.headers.update(headers)
    return response


ASSET_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
ASSET_NAME_RE = re.compile(r"^p\d+(?:-[ftm]\d+)?\.webp$")


@app.api_route("/data/assets/{file_hash}/{name}", methods=["GET", "HEAD"])
def serve_asset(file_hash: str, name: str):
    """Figure / table / formula crops and page images cut from the PDFs (see page_assets). Same access rule as PDFs."""
    if not ASSET_HASH_RE.match(file_hash) or not ASSET_NAME_RE.match(name):
        raise HTTPException(404, "Not found")
    target = os.path.join(page_assets.book_dir(file_hash), name)
    if not os.path.isfile(target):
        raise HTTPException(404, "Not found")
    return FileResponse(target, media_type="image/webp", headers={"Cache-Control": "public, max-age=86400"})


@app.api_route("/data/{file_path:path}", methods=["GET", "HEAD"])
def serve_pdf(file_path: str, request: Request):
    """Serve PDFs from PDF_DIR only (not other files under data/, such as admin settings or staged uploads).

    With STORAGE_BACKEND=s3, `/data/pdfs/<name>` redirects to a short-lived signed S3 URL (PDF_SERVE_MODE=redirect;
    the viewer's `#page=N` survives the redirect) or streams the object (PDF_SERVE_MODE=proxy).
    """
    if settings.storage_backend == "s3":
        return serve_s3_pdf(file_path, request)
    target = os.path.realpath(os.path.join(DATA_DIR, file_path))
    pdf_root = os.path.realpath(os.path.join(PROJECT_ROOT, settings.pdf_dir))
    if not target.lower().endswith(".pdf") or os.path.commonpath([target, pdf_root]) != pdf_root:
        raise HTTPException(404, "Not found")
    if not os.path.isfile(target):
        raise HTTPException(404, "Not found")
    return FileResponse(target, media_type="application/pdf")


def serve_s3_pdf(file_path: str, request: Request):
    from botocore.exceptions import ClientError  # pylint: disable=import-outside-toplevel
    from storage import PDF_URL_PREFIX, get_storage  # pylint: disable=import-outside-toplevel

    prefix = PDF_URL_PREFIX[len("/data/"):]
    relative = file_path[len(prefix):] if file_path.startswith(prefix) else ""
    if not relative.lower().endswith(".pdf") or ".." in relative.split("/"):
        raise HTTPException(404, "Not found")
    storage = get_storage()
    path = storage.path_for(relative)
    try:
        if settings.pdf_serve_mode == "proxy":
            chunks, size = storage.stream(path)
            headers = {"Content-Length": str(size)} if size is not None else {}
            if request.method == "HEAD":
                return Response(media_type="application/pdf", headers=headers)
            return StreamingResponse(chunks, media_type="application/pdf", headers=headers)
        if not storage.exists(path):
            raise HTTPException(404, "Not found")
        return RedirectResponse(storage.signed_url(path), status_code=302)
    except ClientError as exc:
        raise HTTPException(404, "Not found") from exc


class SearchRequest(BaseModel):
    """Request payload for semantic search."""

    query: str = Field(..., min_length=1)
    # Omitted: every hit scoring at least RESULT_RELATIVE_SCORE x the best one, up to MAX_RESULTS.
    top_k: Optional[int] = Field(default=None, ge=1, le=10)
    # Search inside one book (the library's "search in this book").
    book_id: Optional[int] = Field(default=None, ge=1)
    # Search inside several books (a research project's books). Combined with book_id if both are given.
    book_ids: Optional[List[int]] = Field(default=None, min_length=1, max_length=200)

    @field_validator("book_ids")
    @classmethod
    def _positive_ids(cls, value):
        if value is not None and any(item < 1 for item in value):
            raise ValueError("book_ids must be positive")
        return value


class SearchResultItem(BaseModel):
    """Frontend-friendly result format."""

    exact_quote: str
    book_title: str
    author: str
    department: Optional[str] = None
    page_number: int
    pdf_link: str
    paragraph_text: str
    answer_text: Optional[str] = None


class SearchResponse(BaseModel):
    """Search response payload."""

    query: str
    results: List[SearchResultItem]


class RichResultItem(SearchResultItem):
    """A result plus the page content the web UI lays out (see layout.py)."""

    kind: str = "text"
    blocks: List[Dict[str, Any]] = Field(default_factory=list)
    figures: List[Dict[str, Any]] = Field(default_factory=list)
    tables: List[Dict[str, Any]] = Field(default_factory=list)
    formulas: List[Dict[str, Any]] = Field(default_factory=list)
    page: Optional[Dict[str, Any]] = None
    # Concepts the hit names, for the Context Explorer ({id, label, book_count}).
    concepts: List[Dict[str, Any]] = Field(default_factory=list)
    # The book's library page id (the plain /api/search contract stays without it).
    book_id: Optional[int] = None
    # The number printed on the page when it differs from the PDF position (page_number), e.g. "233" or "xii".
    printed_page: Optional[str] = None


class LayoutInfo(BaseModel):
    kind: str
    hero: Optional[int] = None
    intents: List[str] = Field(default_factory=list)


class RichSearchResponse(BaseModel):
    query: str
    layout: LayoutInfo
    results: List[RichResultItem]


def map_result(item: Dict[str, Any]) -> SearchResultItem:
    """Map internal search result to API response contract."""
    source = item.get("source", {})
    pdf_link = admin.pdf_link(source.get("file_path") or "")

    answer_text = (item.get("qa_answer") or "").strip() if item.get("qa_score", 0.0) >= ANSWER_MIN_SCORE else ""

    return SearchResultItem(
        exact_quote=item.get("quote", ""),
        book_title=source.get("book_title") or "Unknown Title",
        author=source.get("author") or "Unknown Author",
        department=source.get("department"),
        page_number=int(source.get("page_number") or 0),
        pdf_link=pdf_link or "#",
        paragraph_text=item.get("matched_paragraph") or item.get("chunk_preview", ""),
        answer_text=answer_text or None,
    )


@app.get("/ready")
def ready():
    """Load balancer health check: 200 when this copy can answer searches, else 503 (see replica.readiness)."""
    state = replica.readiness()
    return JSONResponse(state, status_code=200 if state["ready"] else 503)


@app.get("/health")
def health() -> Dict[str, Any]:
    """Lightweight health endpoint."""
    return {"status": "ok", "version": admin_api.VERSION, "admin_enabled": bool(settings.admin_token)}


def client_address(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def refuse(status: int, detail: str, retry_after: float) -> JSONResponse:
    load_control.metrics.refused(status)
    return JSONResponse({"detail": detail}, status_code=status, headers={"Retry-After": str(max(1, round(retry_after)))})


async def run_search(payload: SearchRequest, request: Request):
    """Rate limit, answer cache, search queue and the pipeline; returns the pipeline result or a refusal Response.

    Order: per-client rate limit (429) -> answer cache -> search queue (503 when full) -> the pipeline in a worker
    thread. See README "Capacity and load".
    """
    client_id = getattr(request.state, "api_client_id", None)
    if client_id is not None:
        wait = load_control.limiter.check(f"key:{client_id}", settings.api_key_rate_limit_per_minute)
    else:
        wait = load_control.limiter.check(f"ip:{client_address(request)}", settings.search_rate_limit_per_minute)
    if wait:
        return refuse(429, "Too many searches; please slow down.", wait)

    started = time.perf_counter()
    # A book-scoped search is a different question: the book is part of the cache key.
    scope_ids = tuple(sorted({*(payload.book_ids or ()), *(() if payload.book_id is None else (payload.book_id,))}))
    if not scope_ids:
        scope = payload.top_k
    elif payload.book_ids is None:
        scope = (payload.top_k, "book", payload.book_id)  # the single-book key, unchanged
    else:
        scope = (payload.top_k, "books", scope_ids)
    cache_key = load_control.cache.key(payload.query, scope)
    result = load_control.cache.get(cache_key)
    cached = result is not None
    timings: Dict[str, float] = {}
    if not cached:
        try:
            scope_args = {"book_id": payload.book_id}
            if payload.book_ids is not None:
                scope_args["book_ids"] = payload.book_ids
            result = await load_control.gate().run(
                lambda: run_in_threadpool(search_and_extract, payload.query, payload.top_k, timings=timings, **scope_args)
            )
        except load_control.Busy as exc:
            return refuse(503, str(exc), exc.retry_after)
        except Exception as exc:  # pylint: disable=broad-except
            raise HTTPException(status_code=500, detail=f"Search execution failed: {exc}") from exc
        load_control.cache.put(cache_key, result)
    load_control.metrics.record(time.perf_counter() - started, cached, timings)
    return result


@app.post("/api/search", response_model=SearchResponse)
async def api_search(payload: SearchRequest, request: Request):
    """Execute semantic retrieval + extractive QA and return normalized results."""
    result = await run_search(payload, request)
    if isinstance(result, Response):
        return result
    mapped = [map_result(item) for item in result.get("results", [])]
    return SearchResponse(query=result.get("query", payload.query), results=mapped)


def book_hashes(file_paths: List[str]) -> Dict[str, str]:
    """file_path -> content hash (the page-asset cache key) for the books in a result list."""
    paths = sorted({path for path in file_paths if path})
    if not paths:
        return {}
    try:
        with db() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT file_path, file_hash FROM books WHERE file_path = ANY(%s)", (paths,))
                rows = cursor.fetchall()
    except Exception as exc:  # pylint: disable=broad-except
        print(f"[OSISS] Book hashes unavailable (no page content in results): {exc}")
        return {}
    return {path: file_hash for path, file_hash in rows if file_hash}


def printed_pages(items: List[Dict[str, Any]]) -> List[Optional[str]]:
    """Printed page label per result (None where unknown), from the page offset / PDF labels kept in `books`."""
    paths = sorted({item.get("source", {}).get("file_path") for item in items if item.get("source", {}).get("file_path")})
    if not paths:
        return [None] * len(items)
    try:
        with db() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT file_path, page_offset, page_labels FROM books WHERE file_path = ANY(%s)", (paths,))
                info = {path: (offset, labels or None) for path, offset, labels in cursor.fetchall()}
    except Exception as exc:  # pylint: disable=broad-except
        print(f"[OSISS] Printed page numbers unavailable: {exc}")
        return [None] * len(items)
    result = []
    for item in items:
        source = item.get("source", {})
        offset, labels = info.get(source.get("file_path"), (None, None))
        result.append(page_labels.printed_label(source.get("page_number") or 0, offset, labels))
    return result


def enrich(query: str, items: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    if not settings.page_assets:
        return layout.enrich_results(query, items, {}, None, page_assets.asset_url)
    hashes = book_hashes([item.get("source", {}).get("file_path") for item in items])
    ocr = page_assets.add_ocr_text if settings.ocr_highlight else None
    return layout.enrich_results(query, items, hashes, page_assets.ensure_pages, page_assets.asset_url, ocr_page=ocr)


@app.post("/api/search/rich", response_model=RichSearchResponse)
async def api_search_rich(payload: SearchRequest, request: Request):
    """Like /api/search, plus figures, tables, formulas, structured blocks, a page image with the hit highlighted
    and a suggested layout. Used by the web UI; the plain endpoint's contract is unchanged."""
    result = await run_search(payload, request)
    if isinstance(result, Response):
        return result
    items = result.get("results", [])
    query = result.get("query", payload.query)
    rich, layout_info = await run_in_threadpool(enrich, query, items)
    named = await run_in_threadpool(
        lambda: [
            concept_api.concepts_in_text([item.get("quote", ""), item.get("matched_paragraph", "")], query=query)
            for item in items
        ]
    )
    labels = await run_in_threadpool(printed_pages, items)
    results = [
        RichResultItem(
            **map_result(item).model_dump(), **extra, concepts=found, book_id=item.get("source", {}).get("book_id"),
            printed_page=label,
        )
        for item, extra, found, label in zip(items, rich, named, labels)
    ]
    return RichSearchResponse(query=query, layout=LayoutInfo(**layout_info), results=results)


if settings.serve_frontend and os.path.isdir(FRONTEND_DIST):
    # Added last so the API routes above take precedence.
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
