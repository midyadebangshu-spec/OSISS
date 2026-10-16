"""Background ingestion inside the API process, controlled from the admin app.

One worker thread runs every job in order: uploads, re-ingests, restores, directory scans and maintenance scripts.
When polling is on it also queues a scan of PDF_DIR every POLLING_INTERVAL_SECONDS (the same work as
`ingestion.sh`). It reuses the API's already-loaded BGE-M3, so no second copy of the model is put on the GPU.
`ingestion.sh` can still run separately; both take the same PostgreSQL advisory lock per book.

Jobs and the log live in memory and are lost on restart; the polling switch is persisted.
"""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
import threading
import time
import traceback
import uuid
from typing import Any, Callable, Dict, List, Optional

from config import settings
from job_store import Job, MemoryJobStore, PostgresJobStore  # noqa: F401  (Job is re-exported)
from replica import replica_id

SRC_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SRC_DIR)

# Maintenance scripts the admin app may run, as (script, arguments).
MAINTENANCE = {
    "db-init": ("db_init.py", []),
    "reindex": ("reindex_es.py", []),
    "backfill-exercises": ("backfill_exercise_flags.py", []),
}


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
    """Queue front-end for the admin API plus, when enabled, the thread that runs the jobs.

    Every API copy has one, so any copy can queue jobs and show their status; only copies started with
    `run_worker=True` (INGEST_WORKER=true) execute them.
    """

    def __init__(
        self,
        connect: Callable[[], Any],
        es_client: Callable[[], Any],
        embedder: Callable[[], Any],
        log_size: int = 2000,
        on_corpus_change: Optional[Callable[[], None]] = None,
        store=None,
        worker_id: Optional[str] = None,
    ):
        self._connect, self._es_client, self._embedder = connect, es_client, embedder
        # Called after a job that may have added, changed or removed books (e.g. to clear the answer cache).
        self._on_corpus_change = on_corpus_change
        if store is None:
            store = PostgresJobStore() if settings.state_backend == "postgres" else MemoryJobStore(log_size)
        self.store = store
        self.worker_id = worker_id or replica_id()
        self._connection = None
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._thread_ident: Optional[int] = None
        self.run_worker = False
        self.polling = False
        self._next_scan = 0.0

    # --- lifecycle ----------------------------------------------------------------------------------------------

    def start(self, polling: bool = False, run_worker: bool = True) -> None:
        self.polling = polling
        self.run_worker = run_worker
        if not run_worker or (self._thread and self._thread.is_alive()):
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
        self.store.add(job)
        self.log(f"[OSISS] Queued: {label}")
        self._wake.set()
        return job

    def cancel(self, job_id: str) -> Job:
        job = self.store.cancel(job_id)
        if job.status == "cancelled":
            self._cleanup(job)
        return job

    def get(self, job_id: str) -> Job:
        return self.store.get(job_id)

    def jobs(self) -> List[Dict[str, Any]]:
        return [job.public() for job in self.store.list()]

    def has_pending(self, kind: str) -> bool:
        return self.store.has_pending(kind)

    def state(self) -> Dict[str, Any]:
        running = self.store.running()
        return {
            "running": bool(self._thread and self._thread.is_alive()),
            "worker_here": self.run_worker,
            "worker_id": self.worker_id,
            "polling": self.polling,
            "poll_interval_seconds": settings.polling_interval_seconds,
            "last_scan_at": self.store.last_finished("scan"),
            "queued": self.store.queued_count(),
            "current": running[0].public() if running else None,
            "running_jobs": [job.public() for job in running],
        }

    # --- log ------------------------------------------------------------------------------------------------------

    def log(self, message: str, echo: bool = True) -> None:
        try:
            self.store.log(message, self.worker_id)
        except Exception as exc:  # pylint: disable=broad-except
            echo = True
            message = f"{message}  (not logged: {exc})"
        if echo:
            target = sys.stdout._target if isinstance(sys.stdout, _ThreadTee) else sys.stdout
            print(message, file=target, flush=True)

    def logs_since(self, seq: int = 0, limit: int = 500) -> Dict[str, Any]:
        entries = self.store.logs_since(seq, limit)
        return {"entries": entries, "last_seq": entries[-1]["seq"] if entries else seq}

    # --- worker loop ----------------------------------------------------------------------------------------------

    def _run(self) -> None:
        self._thread_ident = threading.get_ident()
        try:
            stale_before = time.time() - 3 * settings.replica_heartbeat_seconds
            failed = self.store.fail_interrupted(self.worker_id, stale_before)
            if failed:
                self.log(f"[OSISS] Marked {failed} job(s) interrupted by a stopped worker as failed.")
        except Exception as exc:  # pylint: disable=broad-except
            self.log(f"[OSISS] Could not check for interrupted jobs: {exc}")
        while not self._stop.is_set():
            try:
                job = self._next_job()
            except Exception as exc:  # pylint: disable=broad-except
                # e.g. the database is restarting: keep the worker alive and try again shortly.
                self.log(f"[OSISS] Job queue unavailable: {exc}")
                self._stop.wait(5)
                continue
            if job is None:
                self._wake.wait(timeout=1.0)
                self._wake.clear()
                continue
            self._execute(job)

    def _next_job(self) -> Optional[Job]:
        if settings.state_backend == "postgres":
            import runtime_config  # pylint: disable=import-outside-toplevel

            # Polling may have been switched on or off through another API copy.
            self.polling = bool(runtime_config.get_state("ingester_polling", self.polling))
        if self.polling and time.time() >= self._next_scan and not self.store.has_pending("scan"):
            self.submit("scan", "Scan PDF directory")
            self._next_scan = time.time() + settings.polling_interval_seconds
        return self.store.claim(self.worker_id)

    def _db(self):
        if self._connection is None or self._connection.closed:
            self._connection = self._connect()
        return self._connection

    def _execute(self, job: Job) -> None:
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
            try:
                self.store.save(job)
            except Exception as exc:  # pylint: disable=broad-except
                self.log(f"[OSISS] Could not record the result of '{job.label}': {exc}")
            # A failed re-ingest may already have removed the book, so non-scan jobs count as changes either way.
            if self._on_corpus_change and (job.kind != "scan" or job.params.get("ingested")):
                self._on_corpus_change()

    @staticmethod
    def _cleanup(job: Job) -> None:
        staged = job.params.get("staged_path")
        if staged and os.path.exists(staged):
            os.remove(staged)

    # --- job kinds ------------------------------------------------------------------------------------------------

    def _job_scan(self, job: Job) -> str:
        import ingest  # pylint: disable=import-outside-toplevel

        count = ingest.scan_once(self._db(), self._es_client(), self._embedder(), log=self.log)
        job.params["ingested"] = count
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
