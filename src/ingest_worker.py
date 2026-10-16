"""Background ingestion inside the API process, controlled from the admin app.

One worker thread runs every job in order: uploads, re-ingests, restores, directory scans and maintenance scripts.
When polling is on it also queues a scan of PDF_DIR every POLLING_INTERVAL_SECONDS (the same work as
`ingestion.sh`). It reuses the API's already-loaded BGE-M3, so no second copy of the model is put on the GPU.
`ingestion.sh` can still run separately; both take the same PostgreSQL advisory lock per book.

Jobs and the log live in memory and are lost on restart; the polling switch is persisted.
"""

from __future__ import annotations

import io
import itertools
import os
import shutil
import subprocess
import sys
import threading
import time
import traceback
import uuid
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Deque, Dict, List, Optional

from config import settings

SRC_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SRC_DIR)

# Maintenance scripts the admin app may run, as (script, arguments).
MAINTENANCE = {
    "db-init": ("db_init.py", []),
    "reindex": ("reindex_es.py", []),
    "backfill-exercises": ("backfill_exercise_flags.py", []),
}


@dataclass
class Job:
    """One unit of work for the worker thread."""

    id: str
    kind: str  # upload | reingest | restore | scan | maintenance
    label: str
    status: str = "queued"  # queued | running | done | failed | cancelled
    message: str = ""
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    book_id: Optional[int] = None
    params: Dict[str, Any] = field(default_factory=dict, repr=False)

    def public(self) -> Dict[str, Any]:
        data = asdict(self)
        data.pop("params")
        return data


class _ThreadTee(io.TextIOBase):
    """stdout wrapper that also copies what the worker thread prints (e.g. OCR progress) into its log."""

    def __init__(self, target, thread_ident: Callable[[], Optional[int]], sink: Callable[[str], None]):
        super().__init__()
        self._target, self._thread_ident, self._sink = target, thread_ident, sink
        self._buffer = ""

    def write(self, text: str) -> int:
        written = self._target.write(text)
        if threading.get_ident() == self._thread_ident():
            self._buffer += text
            while "\n" in self._buffer:
                line, self._buffer = self._buffer.split("\n", 1)
                if line.strip():
                    self._sink(line, echo=False)
        return written if written is not None else len(text)

    def flush(self) -> None:
        self._target.flush()

    def __getattr__(self, name):
        return getattr(self._target, name)


class IngestWorker:
    """Single background thread processing a FIFO of jobs."""

    def __init__(
        self,
        connect: Callable[[], Any],
        es_client: Callable[[], Any],
        embedder: Callable[[], Any],
        log_size: int = 2000,
    ):
        self._connect, self._es_client, self._embedder = connect, es_client, embedder
        self._connection = None
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._queue: Deque[str] = deque()
        self._jobs: Dict[str, Job] = {}
        self._order: Deque[str] = deque(maxlen=200)
        self._log: Deque[Dict[str, Any]] = deque(maxlen=log_size)
        self._seq = itertools.count(1)
        self._thread: Optional[threading.Thread] = None
        self._thread_ident: Optional[int] = None
        self._current: Optional[str] = None
        self.polling = False
        self._next_scan = 0.0
        self.last_scan_at: Optional[float] = None

    # --- lifecycle ----------------------------------------------------------------------------------------------

    def start(self, polling: bool = False) -> None:
        self.polling = polling
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        if not isinstance(sys.stdout, _ThreadTee):
            sys.stdout = _ThreadTee(sys.stdout, lambda: self._thread_ident, self.log)
        self._thread = threading.Thread(target=self._run, name="osiss-ingest-worker", daemon=True)
        self._thread.start()

    def shutdown(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout)
        if self._connection is not None:
            try:
                self._connection.close()
            except Exception:  # pylint: disable=broad-except
                pass

    def set_polling(self, enabled: bool) -> None:
        self.polling = enabled
        self._next_scan = 0.0
        self.log(f"[OSISS] Directory polling {'started' if enabled else 'stopped'}.")
        self._wake.set()

    # --- jobs -----------------------------------------------------------------------------------------------------

    def submit(self, kind: str, label: str, book_id: Optional[int] = None, **params) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], kind=kind, label=label, book_id=book_id, params=params)
        with self._lock:
            self._jobs[job.id] = job
            if len(self._order) == self._order.maxlen:
                self._jobs.pop(self._order[0], None)
            self._order.append(job.id)
            self._queue.append(job.id)
        self.log(f"[OSISS] Queued: {label}")
        self._wake.set()
        return job

    def cancel(self, job_id: str) -> Job:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(job_id)
            if job.status == "queued":
                self._queue.remove(job_id)
                job.status, job.finished_at, job.message = "cancelled", time.time(), "Cancelled before it started."
                self._cleanup(job)
        return job

    def get(self, job_id: str) -> Job:
        with self._lock:
            return self._jobs[job_id]

    def jobs(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [self._jobs[job_id].public() for job_id in reversed(self._order) if job_id in self._jobs]

    def has_pending(self, kind: str) -> bool:
        with self._lock:
            return any(self._jobs[job_id].kind == kind for job_id in self._queue)

    def state(self) -> Dict[str, Any]:
        with self._lock:
            queued = len(self._queue)
            current = self._jobs[self._current].public() if self._current else None
        return {
            "running": bool(self._thread and self._thread.is_alive()),
            "polling": self.polling,
            "poll_interval_seconds": settings.polling_interval_seconds,
            "last_scan_at": self.last_scan_at,
            "queued": queued,
            "current": current,
        }

    # --- log ------------------------------------------------------------------------------------------------------

    def log(self, message: str, echo: bool = True) -> None:
        entry = {"seq": next(self._seq), "time": time.time(), "message": message}
        with self._lock:
            self._log.append(entry)
        if echo:
            target = sys.stdout._target if isinstance(sys.stdout, _ThreadTee) else sys.stdout
            print(message, file=target, flush=True)

    def logs_since(self, seq: int = 0, limit: int = 500) -> Dict[str, Any]:
        with self._lock:
            entries = [entry for entry in self._log if entry["seq"] > seq]
        entries = entries[-limit:]
        return {"entries": entries, "last_seq": entries[-1]["seq"] if entries else seq}

    # --- worker loop ----------------------------------------------------------------------------------------------

    def _run(self) -> None:
        self._thread_ident = threading.get_ident()
        while not self._stop.is_set():
            if self.polling and time.time() >= self._next_scan and not self.has_pending("scan"):
                self.submit("scan", "Scan PDF directory")
                self._next_scan = time.time() + settings.polling_interval_seconds
            with self._lock:
                job_id = self._queue.popleft() if self._queue else None
                self._current = job_id
            if job_id is None:
                self._wake.wait(timeout=1.0)
                self._wake.clear()
                continue
            self._execute(self._jobs[job_id])
            with self._lock:
                self._current = None

    def _db(self):
        if self._connection is None or self._connection.closed:
            self._connection = self._connect()
        return self._connection

    def _execute(self, job: Job) -> None:
        job.status, job.started_at = "running", time.time()
        self.log(f"[OSISS] Started: {job.label}")
        try:
            handler = getattr(self, f"_job_{job.kind}")
            job.message = handler(job) or "Done."
            job.status = "done"
            self.log(f"[OSISS] Finished: {job.label} - {job.message}")
        except Exception as exc:  # pylint: disable=broad-except
            job.status, job.message = "failed", str(exc)
            self.log(f"[OSISS] FAILED: {job.label} - {exc}")
            self.log(traceback.format_exc().rstrip(), echo=False)
            if self._connection is not None and not self._connection.closed:
                try:
                    self._connection.rollback()
                except Exception:  # pylint: disable=broad-except
                    self._connection = None
        finally:
            job.finished_at = time.time()
            self._cleanup(job)

    @staticmethod
    def _cleanup(job: Job) -> None:
        staged = job.params.get("staged_path")
        if staged and os.path.exists(staged):
            os.remove(staged)

    # --- job kinds ------------------------------------------------------------------------------------------------

    def _job_scan(self, job: Job) -> str:
        import ingest  # pylint: disable=import-outside-toplevel

        count = ingest.scan_once(self._db(), self._es_client(), self._embedder(), log=self.log)
        self.last_scan_at = time.time()
        return f"{count} book(s) ingested."

    def _job_upload(self, job: Job) -> str:
        """Move a staged upload into PDF_DIR and ingest it with the admin's metadata, under the lock."""
        import admin  # pylint: disable=import-outside-toplevel
        import ingest  # pylint: disable=import-outside-toplevel

        params = job.params
        connection = self._db()
        with ingest.ingest_lock(connection):
            duplicate = admin.find_book_by_hash(connection, params["file_hash"])
            connection.rollback()
            if duplicate:
                job.book_id = duplicate["id"]
                raise RuntimeError(f"Same file is already ingested as book {duplicate['id']} ('{duplicate['title']}').")
            os.makedirs(settings.pdf_dir, exist_ok=True)
            target = admin.unique_pdf_path(settings.pdf_dir, params["file_name"])
            shutil.move(params["staged_path"], target)
            ok, message = ingest.process_pdf(
                target, connection, self._es_client(), self._embedder(), params["file_hash"], params["metadata"]
            )
            if not ok:
                # Keep the file but exclude it, so the poller does not retry it with file-name metadata.
                self._exclude_failed(connection, target, params)
                raise RuntimeError(f"{message} The PDF was kept as {target} and listed under deleted books.")
            job.book_id = self._book_id_for(connection, target)
        return message

    def _exclude_failed(self, connection, path: str, params: Dict[str, Any]) -> None:
        from psycopg2.extras import Json  # pylint: disable=import-outside-toplevel

        connection.rollback()
        with connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO excluded_files (file_path, file_hash, metadata) VALUES (%s, %s, %s) "
                    "ON CONFLICT (file_path) DO NOTHING",
                    (path, params["file_hash"], Json(params["metadata"])),
                )

    @staticmethod
    def _book_id_for(connection, path: str) -> Optional[int]:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id FROM books WHERE file_path = %s", (path,))
            row = cursor.fetchone()
        connection.rollback()
        return row[0] if row else None

    def _job_reingest(self, job: Job) -> str:
        """Re-extract and re-embed a book from its file, keeping its metadata."""
        import admin  # pylint: disable=import-outside-toplevel
        import ingest  # pylint: disable=import-outside-toplevel
        from utils import compute_file_hash  # pylint: disable=import-outside-toplevel

        connection = self._db()
        with ingest.ingest_lock(connection):
            book = admin.get_book(connection, job.book_id)
            connection.rollback()
            path = book["file_path"]
            if not os.path.isfile(path):
                raise RuntimeError(f"PDF file is missing: {path}")
            metadata = {name: book[name] for name in admin.METADATA_FIELDS}
            ingest.delete_book(connection, self._es_client(), job.book_id)
            ok, message = ingest.process_pdf(
                path, connection, self._es_client(), self._embedder(), compute_file_hash(path), metadata
            )
            if not ok:
                self._exclude_failed(connection, path, {"file_hash": book["file_hash"], "metadata": metadata})
                raise RuntimeError(f"{message} The book was removed and listed under deleted books.")
            job.book_id = self._book_id_for(connection, path)
        return message

    def _job_restore(self, job: Job) -> str:
        """Ingest a deleted book's PDF again with the metadata it had."""
        import admin  # pylint: disable=import-outside-toplevel
        import ingest  # pylint: disable=import-outside-toplevel
        from utils import compute_file_hash  # pylint: disable=import-outside-toplevel

        path = job.params["file_path"]
        connection = self._db()
        with ingest.ingest_lock(connection):
            excluded = admin.get_excluded(connection, path)
            connection.rollback()
            if not os.path.isfile(path):
                raise RuntimeError(f"PDF file is missing: {path}")
            metadata = {name: excluded["metadata"].get(name) for name in admin.METADATA_FIELDS}
            if not metadata.get("title"):
                metadata = None
            admin.remove_exclusion(connection, path)
            ok, message = ingest.process_pdf(
                path, connection, self._es_client(), self._embedder(), compute_file_hash(path), metadata
            )
            if not ok:
                self._exclude_failed(
                    connection, path, {"file_hash": excluded["file_hash"], "metadata": excluded["metadata"]}
                )
                raise RuntimeError(message)
            job.book_id = self._book_id_for(connection, path)
        return message

    def _job_maintenance(self, job: Job) -> str:
        """Run one of the repo's maintenance scripts as a subprocess, streaming its output into the log."""
        script, args = MAINTENANCE[job.params["task"]]
        process = subprocess.Popen(
            [sys.executable, "-u", os.path.join(SRC_DIR, script), *args],
            cwd=PROJECT_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        assert process.stdout is not None
        for line in process.stdout:
            if line.strip():
                self.log(line.rstrip(), echo=False)
        code = process.wait()
        if code != 0:
            raise RuntimeError(f"{script} exited with code {code}")
        return f"{script} completed."
