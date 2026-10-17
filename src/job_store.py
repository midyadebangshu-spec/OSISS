"""Where ingestion jobs and the ingestion log are kept.

`MemoryJobStore` (STATE_BACKEND=local) is one process's queue, as before. `PostgresJobStore`
(STATE_BACKEND=postgres) shares the queue and log between API copies: any copy can queue a job or show its status,
and copies running the worker (INGEST_WORKER=true) claim jobs with `FOR UPDATE SKIP LOCKED`, so each job runs once
even with several workers.
"""

from __future__ import annotations

import itertools
import json
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any, Deque, Dict, List, Optional

LOG_KEEP = 5000
JOBS_LISTED = 200


@dataclass
class Job:
    """One unit of work for the ingestion worker."""

    id: str
    kind: str  # upload | reingest | restore | scan | maintenance
    label: str
    status: str = "queued"  # queued | running | done | failed | cancelled
    message: str = ""
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    book_id: Optional[int] = None
    worker: Optional[str] = None
    params: Dict[str, Any] = field(default_factory=dict, repr=False)

    def public(self) -> Dict[str, Any]:
        data = asdict(self)
        data.pop("params")
        return data


class MemoryJobStore:
    """In-process queue and log (one API process)."""

    def __init__(self, log_size: int = 2000):
        self._lock = threading.Lock()
        self._queue: Deque[str] = deque()
        self._jobs: Dict[str, Job] = {}
        self._order: Deque[str] = deque(maxlen=JOBS_LISTED)
        self._log: Deque[Dict[str, Any]] = deque(maxlen=log_size)
        self._seq = itertools.count(1)

    def add(self, job: Job) -> None:
        with self._lock:
            if len(self._order) == self._order.maxlen:
                self._jobs.pop(self._order[0], None)
            self._jobs[job.id] = job
            self._order.append(job.id)
            self._queue.append(job.id)

    def get(self, job_id: str) -> Job:
        with self._lock:
            return self._jobs[job_id]

    def list(self) -> List[Job]:
        with self._lock:
            return [self._jobs[job_id] for job_id in reversed(self._order) if job_id in self._jobs]

    def claim(self, worker: str) -> Optional[Job]:
        with self._lock:
            if not self._queue:
                return None
            job = self._jobs[self._queue.popleft()]
            job.status, job.started_at, job.worker = "running", time.time(), worker
            return job

    def save(self, job: Job) -> None:
        with self._lock:
            self._jobs[job.id] = job

    def cancel(self, job_id: str) -> Job:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(job_id)
            if job.status == "queued":
                self._queue.remove(job_id)
                job.status, job.finished_at, job.message = "cancelled", time.time(), "Cancelled before it started."
            return job

    def has_pending(self, kind: str) -> bool:
        with self._lock:
            return any(self._jobs[job_id].kind == kind for job_id in self._queue)

    def queued_count(self) -> int:
        with self._lock:
            return len(self._queue)

    def running(self) -> List[Job]:
        with self._lock:
            return [job for job in self._jobs.values() if job.status == "running"]

    def last_finished(self, kind: str) -> Optional[float]:
        with self._lock:
            times = [job.finished_at for job in self._jobs.values() if job.kind == kind and job.status == "done"]
        return max(times) if times else None

    def fail_interrupted(self, worker: str, stale_before: float) -> int:  # pylint: disable=unused-argument
        return 0  # an in-memory queue does not survive the process

    def log(self, message: str, worker: Optional[str] = None) -> None:
        with self._lock:
            self._log.append({"seq": next(self._seq), "time": time.time(), "message": message, "worker": worker})

    def logs_since(self, seq: int, limit: int) -> List[Dict[str, Any]]:
        with self._lock:
            entries = [entry for entry in self._log if entry["seq"] > seq]
        return entries[-limit:]


_JOB_COLUMNS = "id, kind, label, status, message, created_at, started_at, finished_at, book_id, worker, params"


def _row_to_job(row) -> Job:
    job_id, kind, label, status, message, created, started, finished, book_id, worker, params = row
    return Job(id=job_id, kind=kind, label=label, status=status, message=message, created_at=created,
               started_at=started, finished_at=finished, book_id=book_id, worker=worker, params=params or {})


class PostgresJobStore:
    """Queue and log in the `ingest_jobs` / `ingest_log` tables, shared by all API copies."""

    def __init__(self, db=None):
        if db is None:
            from db_pool import db as pooled  # pylint: disable=import-outside-toplevel

            db = pooled
        self._db = db
        self._log_writes = itertools.count()

    def _query(self, sql: str, params=(), fetch: str = "none"):
        with self._db() as connection:
            with connection:
                with connection.cursor() as cursor:
                    cursor.execute(sql, params)
                    if fetch == "one":
                        return cursor.fetchone()
                    if fetch == "all":
                        return cursor.fetchall()
                    return cursor.rowcount

    def add(self, job: Job) -> None:
        self._query(
            "INSERT INTO ingest_jobs (id, kind, label, status, message, created_at, book_id, params) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (job.id, job.kind, job.label, job.status, job.message, job.created_at, job.book_id, json.dumps(job.params)),
        )

    def get(self, job_id: str) -> Job:
        row = self._query(f"SELECT {_JOB_COLUMNS} FROM ingest_jobs WHERE id = %s", (job_id,), "one")
        if row is None:
            raise KeyError(job_id)
        return _row_to_job(row)

    def list(self) -> List[Job]:
        rows = self._query(f"SELECT {_JOB_COLUMNS} FROM ingest_jobs ORDER BY seq DESC LIMIT %s", (JOBS_LISTED,), "all")
        return [_row_to_job(row) for row in rows]

    def claim(self, worker: str) -> Optional[Job]:
        row = self._query(
            "UPDATE ingest_jobs SET status = 'running', started_at = %s, worker = %s WHERE seq = ("
            "  SELECT seq FROM ingest_jobs WHERE status = 'queued' ORDER BY seq FOR UPDATE SKIP LOCKED LIMIT 1"
            f") RETURNING {_JOB_COLUMNS}",
            (time.time(), worker),
            "one",
        )
        return _row_to_job(row) if row else None

    def save(self, job: Job) -> None:
        self._query(
            "UPDATE ingest_jobs SET status = %s, message = %s, started_at = %s, finished_at = %s, book_id = %s, "
            "params = %s WHERE id = %s",
            (job.status, job.message, job.started_at, job.finished_at, job.book_id, json.dumps(job.params), job.id),
        )

    def cancel(self, job_id: str) -> Job:
        self._query(
            "UPDATE ingest_jobs SET status = 'cancelled', finished_at = %s, message = 'Cancelled before it started.' "
            "WHERE id = %s AND status = 'queued'",
            (time.time(), job_id),
        )
        return self.get(job_id)

    def has_pending(self, kind: str) -> bool:
        return bool(self._query("SELECT 1 FROM ingest_jobs WHERE status = 'queued' AND kind = %s LIMIT 1", (kind,), "one"))

    def queued_count(self) -> int:
        return self._query("SELECT COUNT(*) FROM ingest_jobs WHERE status = 'queued'", (), "one")[0]

    def running(self) -> List[Job]:
        rows = self._query(f"SELECT {_JOB_COLUMNS} FROM ingest_jobs WHERE status = 'running' ORDER BY seq", (), "all")
        return [_row_to_job(row) for row in rows]

    def last_finished(self, kind: str) -> Optional[float]:
        row = self._query("SELECT MAX(finished_at) FROM ingest_jobs WHERE kind = %s AND status = 'done'", (kind,), "one")
        return row[0] if row else None

    def fail_interrupted(self, worker: str, stale_before: float) -> int:
        """Mark jobs left 'running' by this worker's previous life, or by a worker that stopped reporting, as failed."""
        return self._query(
            "UPDATE ingest_jobs j SET status = 'failed', finished_at = %s, "
            "message = 'Interrupted: the worker running it stopped. Queue it again.' "
            "WHERE status = 'running' AND (worker = %s OR NOT EXISTS ("
            "  SELECT 1 FROM replicas r WHERE r.id = j.worker AND r.heartbeat_at > %s))",
            (time.time(), worker, stale_before),
        )

    def log(self, message: str, worker: Optional[str] = None) -> None:
        self._query("INSERT INTO ingest_log (time, message, worker) VALUES (%s, %s, %s)", (time.time(), message, worker))
        if next(self._log_writes) % 200 == 0:
            self._query("DELETE FROM ingest_log WHERE seq <= (SELECT MAX(seq) FROM ingest_log) - %s", (LOG_KEEP,))

    def logs_since(self, seq: int, limit: int) -> List[Dict[str, Any]]:
        rows = self._query(
            "SELECT seq, time, message, worker FROM ("
            "  SELECT seq, time, message, worker FROM ingest_log WHERE seq > %s ORDER BY seq DESC LIMIT %s"
            ") recent ORDER BY seq",
            (seq, limit),
            "all",
        )
        return [{"seq": s, "time": t, "message": m, "worker": w} for s, t, m, w in rows]
