"""Admin API (/api/admin/*) used by the OSISS Admin desktop app.

Every route needs the `X-Admin-Token` header to equal ADMIN_TOKEN. Without ADMIN_TOKEN the routes answer 503.
Repeated wrong tokens from one address are blocked for a while (429), and ADMIN_ALLOWED_IPS can restrict which
addresses may call the routes at all.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import os
import shutil
import threading
import time
from collections import defaultdict, deque
from urllib.parse import quote
from typing import Any, Deque, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

import admin
import load_control
import runtime_config
from config import settings
from db_pool import db

VERSION = "1.1.0"
STARTED_AT = time.time()

# Wrong-token lockout: this many failures within the window block the address for the window.
MAX_AUTH_FAILURES = 10
AUTH_WINDOW_SECONDS = 15 * 60
MIN_TOKEN_LENGTH = 24
# A delete waits this long for a running ingestion (e.g. a long OCR job) before answering 409.
DELETE_LOCK_TIMEOUT_SECONDS = 10


class _Context:
    """Process-wide pieces the routes use; replaced by tests."""

    worker = None  # ingest_worker.IngestWorker
    es_client = None  # callable returning an Elasticsearch client
    search = None  # callable(query, top_k, timings) -> dict


context = _Context()


# --- auth ---------------------------------------------------------------------------------------------------------


class AuthFailures:
    """Wrong-token attempts per client address."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._failures: Dict[str, Deque[float]] = defaultdict(deque)

    def _prune(self, address: str, now: float) -> Deque[float]:
        attempts = self._failures[address]
        while attempts and attempts[0] <= now - AUTH_WINDOW_SECONDS:
            attempts.popleft()
        return attempts

    def blocked_for(self, address: str) -> float:
        """Seconds until the address may try again (0 = not blocked)."""
        now = time.time()
        with self._lock:
            attempts = self._prune(address, now)
            if len(attempts) < MAX_AUTH_FAILURES:
                return 0.0
            return attempts[-MAX_AUTH_FAILURES] + AUTH_WINDOW_SECONDS - now

    def record(self, address: str) -> None:
        with self._lock:
            self._prune(address, time.time()).append(time.time())

    def clear(self, address: str) -> None:
        with self._lock:
            self._failures.pop(address, None)


auth_failures = AuthFailures()


def allowed_networks() -> List[Any]:
    networks = []
    for item in settings.admin_allowed_ips.split(","):
        if item.strip():
            networks.append(ipaddress.ip_network(item.strip(), strict=False))
    return networks


def client_address(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def require_admin(request: Request) -> None:
    """FastAPI dependency guarding every admin route."""
    if not settings.admin_token:
        raise HTTPException(503, "Admin API is disabled. Set ADMIN_TOKEN on the server and restart it.")

    address = client_address(request)
    networks = allowed_networks()
    if networks:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            ip = None
        if ip is None or not any(ip in network for network in networks):
            raise HTTPException(403, f"Admin access is not allowed from {address}.")

    wait = auth_failures.blocked_for(address)
    if wait > 0:
        raise HTTPException(
            429, "Too many wrong admin tokens; try again later.", headers={"Retry-After": str(int(wait) + 1)}
        )

    supplied = request.headers.get("x-admin-token", "")
    if not hmac.compare_digest(
        hashlib.sha256(supplied.encode()).digest(), hashlib.sha256(settings.admin_token.encode()).digest()
    ):
        auth_failures.record(address)
        raise HTTPException(401, "Wrong or missing admin token.")
    auth_failures.clear(address)


router = APIRouter(prefix="/api/admin", dependencies=[Depends(require_admin)])


def _fail(exc: admin.AdminError):
    raise HTTPException(exc.status, str(exc)) from exc


def _worker():
    if context.worker is None:
        raise HTTPException(503, "Ingestion worker is not running.")
    return context.worker


# --- status -------------------------------------------------------------------------------------------------------


def _postgres_status() -> Dict[str, Any]:
    try:
        with db() as connection:
            return {"ok": True, **admin.counts(connection)}
    except Exception as exc:  # pylint: disable=broad-except
        return {"ok": False, "error": str(exc).strip()}


def _elasticsearch_status() -> Dict[str, Any]:
    from elasticsearch import Elasticsearch  # pylint: disable=import-outside-toplevel

    try:
        from clients import elasticsearch_kwargs  # pylint: disable=import-outside-toplevel

        client = Elasticsearch(settings.elasticsearch_url, request_timeout=3, **elasticsearch_kwargs())
        health = client.cluster.health(request_timeout=3)
        index = settings.elasticsearch_index
        exists = bool(client.indices.exists(index=index))
        documents = client.count(index=index)["count"] if exists else 0
        return {
            "ok": True,
            "cluster_status": health.get("status"),
            "index": index,
            "index_exists": exists,
            "documents": documents,
        }
    except Exception as exc:  # pylint: disable=broad-except
        return {"ok": False, "error": str(exc).strip()[:300]}


def _models_status() -> List[Dict[str, Any]]:
    loaded = {}
    try:
        import search  # pylint: disable=import-outside-toplevel
        import translit  # pylint: disable=import-outside-toplevel

        loaded = {
            "embedder": search.get_models.cache_info().currsize > 0,
            "qa": search.get_models.cache_info().currsize > 0,
            "reranker": search.get_reranker.cache_info().currsize > 0,
            "translit": translit.get_translator.cache_info().currsize > 0,
        }
    except Exception:  # pylint: disable=broad-except
        pass
    models = [
        ("embedder", "BGE-M3 embedder", settings.bge_model_path, True),
        ("qa", "XLM-R SQuAD2 QA", settings.qa_model_path, True),
        ("reranker", "BGE reranker", settings.reranker_model_path, settings.rerank_enabled),
        ("translit", "IndicXlit transliteration", settings.translit_model_path, settings.translit_enabled),
    ]
    return [
        {"key": key, "name": name, "path": path, "present": os.path.isdir(path), "enabled": enabled,
         "loaded": loaded.get(key, False)}
        for key, name, path, enabled in models
    ]


def _gpu_status() -> Dict[str, Any]:
    try:
        import torch  # pylint: disable=import-outside-toplevel

        if not torch.cuda.is_available():
            return {"available": False, "device": settings.inference_device}
        free, total = torch.cuda.mem_get_info()
        return {
            "available": True,
            "name": torch.cuda.get_device_name(0),
            "memory_free": free,
            "memory_total": total,
            "device": settings.inference_device,
        }
    except Exception as exc:  # pylint: disable=broad-except
        return {"available": False, "error": str(exc)}


def _disk_status() -> Dict[str, Any]:
    path = settings.pdf_dir if os.path.isdir(settings.pdf_dir) else "."
    usage = shutil.disk_usage(path)
    return {"path": os.path.abspath(path), "total": usage.total, "used": usage.used, "free": usage.free}


@router.get("/status")
def status() -> Dict[str, Any]:
    warnings = []
    if len(settings.admin_token) < MIN_TOKEN_LENGTH:
        warnings.append(f"ADMIN_TOKEN is shorter than {MIN_TOKEN_LENGTH} characters; generate a longer one.")
    public = settings.public_base_url
    if public and settings.search_access != "key" and not public.startswith(("http://localhost", "http://127.")):
        warnings.append("Search is open to anyone who can reach the public URL. Switch access to 'key' if needed.")
    return {
        "version": VERSION,
        "uptime_seconds": time.time() - STARTED_AT,
        "postgres": _postgres_status(),
        "elasticsearch": _elasticsearch_status(),
        "models": _models_status(),
        "gpu": _gpu_status(),
        "disk": _disk_status(),
        "ingester": context.worker.state() if context.worker else {"running": False},
        "access": {
            "mode": settings.search_access or "open",
            "configured": settings.search_access in {"open", "key"},
            "cors_origins": runtime_config.cors_origin_list(),
            "public_base_url": settings.public_base_url,
        },
        "admin_ip_restricted": bool(settings.admin_allowed_ips.strip()),
        "warnings": warnings,
    }


# --- books --------------------------------------------------------------------------------------------------------


class MetadataPatch(BaseModel):
    title: Optional[str] = None
    author: Optional[str] = None
    publication_year: Optional[int] = None
    department: Optional[str] = None


@router.get("/books")
def list_books() -> Dict[str, Any]:
    with db() as connection:
        return {"books": admin.list_books(connection)}


@router.get("/books/deleted")
def list_deleted() -> Dict[str, Any]:
    with db() as connection:
        return {"deleted": admin.list_excluded(connection)}


@router.get("/books/{book_id}")
def get_book(book_id: int) -> Dict[str, Any]:
    try:
        with db() as connection:
            return admin.get_book(connection, book_id)
    except admin.AdminError as exc:
        _fail(exc)


@router.patch("/books/{book_id}")
def update_book(book_id: int, patch: MetadataPatch) -> Dict[str, Any]:
    try:
        with db() as connection:
            book = admin.update_book_metadata(
                connection, context.es_client(), book_id, patch.model_dump(exclude_unset=True)
            )
    except admin.AdminError as exc:
        _fail(exc)
    load_control.cache.invalidate()  # cached answers carry the old title / author
    return book


@router.delete("/books/{book_id}")
def delete_book(book_id: int) -> Dict[str, Any]:
    """Remove the book from search; its PDF stays on disk and is listed under deleted books."""
    import ingest  # pylint: disable=import-outside-toplevel

    try:
        with db() as connection:
            with ingest.ingest_lock(connection, timeout=DELETE_LOCK_TIMEOUT_SECONDS):
                book = admin.exclude_and_delete_book(connection, context.es_client(), book_id)
    except admin.AdminError as exc:
        _fail(exc)
    except ingest.IngestBusy as exc:
        raise HTTPException(409, str(exc)) from exc
    load_control.cache.invalidate()
    return {"deleted": book_id, "file_path": book["file_path"], "file_kept": True}


@router.post("/books/{book_id}/reingest")
def reingest_book(book_id: int) -> Dict[str, Any]:
    try:
        with db() as connection:
            book = admin.get_book(connection, book_id)
    except admin.AdminError as exc:
        _fail(exc)
    job = _worker().submit("reingest", f"Re-ingest '{book['title']}'", book_id=book_id)
    return job.public()


class RestoreRequest(BaseModel):
    file_path: str


@router.post("/books/restore")
def restore_book(payload: RestoreRequest) -> Dict[str, Any]:
    try:
        with db() as connection:
            excluded = admin.get_excluded(connection, payload.file_path)
    except admin.AdminError as exc:
        _fail(exc)
    title = excluded["metadata"].get("title") or os.path.basename(payload.file_path)
    return _worker().submit("restore", f"Restore '{title}'", file_path=payload.file_path).public()


@router.get("/books/{book_id}/pdf")
def book_pdf(book_id: int):
    try:
        with db() as connection:
            book = admin.get_book(connection, book_id)
    except admin.AdminError as exc:
        _fail(exc)
    if not book["file_exists"]:
        raise HTTPException(404, "PDF file is missing on the server.")
    from storage import get_storage, is_s3_path  # pylint: disable=import-outside-toplevel

    name = os.path.basename(book["file_path"])
    if is_s3_path(book["file_path"]):
        # Streamed rather than redirected: the admin app refuses redirects so its token never follows one.
        chunks, size = get_storage().stream(book["file_path"])
        headers = {"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"}
        if size is not None:
            headers["Content-Length"] = str(size)
        return StreamingResponse(chunks, media_type="application/pdf", headers=headers)
    return FileResponse(book["file_path"], media_type="application/pdf", filename=name)


@router.get("/metadata-guess")
def metadata_guess(filename: str) -> Dict[str, Any]:
    from utils import infer_metadata_from_filename  # pylint: disable=import-outside-toplevel

    guess = infer_metadata_from_filename(filename)
    return {name: guess.get(name) for name in admin.METADATA_FIELDS}


@router.post("/books")
def upload_book(
    file: UploadFile = File(...),
    title: str = Form(...),
    author: str = Form(""),
    publication_year: str = Form(""),
    department: str = Form(""),
) -> Dict[str, Any]:
    """Stage an uploaded PDF and queue its ingestion with the given metadata."""
    try:
        metadata = admin.clean_metadata(
            {"title": title, "author": author, "publication_year": publication_year, "department": department}
        )
        file_name = admin.safe_pdf_name(file.filename or "")
    except admin.AdminError as exc:
        _fail(exc)

    from storage import get_storage  # pylint: disable=import-outside-toplevel

    storage = get_storage()
    staged = storage.new_staging_file()
    digest = hashlib.sha256()
    size, limit = 0, settings.upload_max_mb * 1024 * 1024
    try:
        with open(staged, "wb") as handle:
            first = True
            while True:
                block = file.file.read(1 << 20)
                if not block:
                    break
                if first and not block.startswith(b"%PDF-"):
                    raise HTTPException(400, "This file is not a PDF.")
                first = False
                size += len(block)
                if size > limit:
                    raise HTTPException(413, f"PDF is larger than {settings.upload_max_mb} MB.")
                digest.update(block)
                handle.write(block)
        if size == 0:
            raise HTTPException(400, "The uploaded file is empty.")
        file_hash = digest.hexdigest()
        with db() as connection:
            duplicate = admin.find_book_by_hash(connection, file_hash)
        if duplicate:
            raise HTTPException(409, f"This PDF is already ingested as '{duplicate['title']}' (book {duplicate['id']}).")
    except BaseException:
        if os.path.exists(staged):
            os.remove(staged)
        raise
    # Local storage: the file already is in UPLOAD_DIR. S3: it goes to the staging prefix, reachable by any worker.
    staged = storage.stage(staged, file_hash)

    job = _worker().submit(
        "upload", f"Upload '{metadata['title']}'", staged_path=staged, file_name=file_name, file_hash=file_hash,
        metadata=metadata,
    )
    return job.public()


# --- ingester and jobs --------------------------------------------------------------------------------------------


class PollingRequest(BaseModel):
    polling: bool


@router.get("/ingester")
def ingester_state() -> Dict[str, Any]:
    return _worker().state()


@router.post("/ingester")
def set_ingester(payload: PollingRequest) -> Dict[str, Any]:
    worker = _worker()
    worker.set_polling(payload.polling)
    runtime_config.set_state("ingester_polling", payload.polling)
    return worker.state()


@router.post("/ingester/scan")
def scan_now() -> Dict[str, Any]:
    worker = _worker()
    if worker.has_pending("scan"):
        raise HTTPException(409, "A scan is already queued.")
    return worker.submit("scan", "Scan PDF directory").public()


@router.get("/ingester/logs")
def ingester_logs(since: int = 0, limit: int = 500) -> Dict[str, Any]:
    return _worker().logs_since(since, min(max(limit, 1), 2000))


@router.get("/jobs")
def list_jobs() -> Dict[str, Any]:
    return {"jobs": _worker().jobs()}


@router.get("/jobs/{job_id}")
def get_job(job_id: str) -> Dict[str, Any]:
    try:
        return _worker().get(job_id).public()
    except KeyError as exc:
        raise HTTPException(404, "Job not found (jobs are kept in memory until the API restarts).") from exc


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> Dict[str, Any]:
    try:
        job = _worker().cancel(job_id)
    except KeyError as exc:
        raise HTTPException(404, "Job not found.") from exc
    if job.status != "cancelled":
        raise HTTPException(409, f"Job is {job.status}; only queued jobs can be cancelled.")
    return job.public()


@router.post("/maintenance/{task}")
def run_maintenance(task: str) -> Dict[str, Any]:
    from ingest_worker import MAINTENANCE  # pylint: disable=import-outside-toplevel

    if task not in MAINTENANCE:
        raise HTTPException(404, f"Unknown task; choose one of {', '.join(MAINTENANCE)}.")
    return _worker().submit("maintenance", f"Maintenance: {task}", task=task).public()


# --- settings and access ------------------------------------------------------------------------------------------


@router.get("/settings")
def get_settings() -> Dict[str, Any]:
    return runtime_config.describe()


@router.put("/settings")
def put_settings(values: Dict[str, Any]) -> Dict[str, Any]:
    try:
        applied = runtime_config.update(values)
    except runtime_config.SettingsError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"applied": applied, **runtime_config.describe()}


class AccessUpdate(BaseModel):
    search_access: Optional[str] = None
    cors_origins: Optional[List[str]] = None
    public_base_url: Optional[str] = None


def _access() -> Dict[str, Any]:
    return {
        "search_access": settings.search_access or "open",
        "configured": settings.search_access in {"open", "key"},
        "cors_origins": runtime_config.cors_origin_list(),
        "public_base_url": settings.public_base_url,
    }


@router.get("/access")
def get_access() -> Dict[str, Any]:
    return _access()


@router.put("/access")
def put_access(payload: AccessUpdate) -> Dict[str, Any]:
    try:
        runtime_config.update(payload.model_dump(exclude_none=True))
    except runtime_config.SettingsError as exc:
        raise HTTPException(400, str(exc)) from exc
    return _access()


class NewKey(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)


class KeyPatch(BaseModel):
    enabled: bool


@router.get("/keys")
def list_keys() -> Dict[str, Any]:
    with db() as connection:
        return {"keys": admin.list_api_clients(connection)}


@router.post("/keys")
def create_key(payload: NewKey) -> Dict[str, Any]:
    try:
        with db() as connection:
            return admin.create_api_client(connection, payload.name)
    except admin.AdminError as exc:
        _fail(exc)


@router.patch("/keys/{client_id}")
def update_key(client_id: int, payload: KeyPatch) -> Dict[str, Any]:
    try:
        with db() as connection:
            return admin.set_api_client_enabled(connection, client_id, payload.enabled)
    except admin.AdminError as exc:
        _fail(exc)


@router.delete("/keys/{client_id}")
def delete_key(client_id: int) -> Dict[str, Any]:
    try:
        with db() as connection:
            admin.delete_api_client(connection, client_id)
    except admin.AdminError as exc:
        _fail(exc)
    return {"deleted": client_id}


# --- search console -----------------------------------------------------------------------------------------------


class ConsoleSearch(BaseModel):
    query: str = Field(..., min_length=1)
    top_k: int = Field(default=3, ge=1, le=10)


@router.get("/metrics")
def search_metrics() -> Dict[str, Any]:
    """Search load of the last five minutes (Dashboard "Search load" tile)."""
    return load_control.metrics.snapshot(load_control.gate(), load_control.cache)


@router.post("/search")
async def console_search(payload: ConsoleSearch) -> Dict[str, Any]:
    """The full search pipeline with scores and per-stage timings, for testing from the admin app.

    Never cached (it is for testing), but it waits in the same queue as public searches.
    """
    from starlette.concurrency import run_in_threadpool  # pylint: disable=import-outside-toplevel

    timings: Dict[str, float] = {}
    started = time.perf_counter()
    try:
        result = await load_control.gate().run(
            lambda: run_in_threadpool(context.search, payload.query, payload.top_k, timings)
        )
    except load_control.Busy as exc:
        raise HTTPException(503, str(exc), headers={"Retry-After": str(exc.retry_after)}) from exc
    except Exception as exc:  # pylint: disable=broad-except
        raise HTTPException(500, f"Search execution failed: {exc}") from exc
    total = time.perf_counter() - started
    results = []
    for item in result.get("results", []):
        source = item.get("source", {})
        results.append(
            {
                "quote": item.get("quote", ""),
                "answer": item.get("qa_answer", ""),
                "paragraph": item.get("matched_paragraph") or item.get("chunk_preview", ""),
                "book_title": source.get("book_title"),
                "author": source.get("author"),
                "page_number": source.get("page_number"),
                "file_path": source.get("file_path"),
                "pdf_link": admin.pdf_link(source.get("file_path") or ""),
                "retrieval_score": item.get("retrieval_score"),
                "rerank_score": item.get("rerank_score"),
                "qa_score": item.get("qa_score"),
            }
        )
    return {
        "query": result.get("query", payload.query),
        "mode": result.get("mode"),
        "variant_queries": result.get("variant_queries") or {},
        "timings": {**timings, "total": total},
        "results": results,
    }
