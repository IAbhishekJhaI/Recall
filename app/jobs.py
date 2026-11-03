"""In-memory tracking of card-reading jobs (run the app with a single worker process)."""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

JOB_TTL_S = 3600


@dataclass
class Job:
    id: str
    user_id: str
    status: str = "pending"  # pending | done | error
    result: dict[str, Any] | None = None
    error: str | None = None
    created: float = field(default_factory=time.time)


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def _expire(self) -> None:
        cutoff = time.time() - JOB_TTL_S
        for jid in [j.id for j in self._jobs.values() if j.created < cutoff]:
            del self._jobs[jid]

    def create(self, user_id: str) -> Job:
        with self._lock:
            self._expire()
            job = Job(id=uuid.uuid4().hex, user_id=user_id)
            self._jobs[job.id] = job
            return job

    def pending_count(self, user_id: str) -> int:
        with self._lock:
            self._expire()
            return sum(1 for j in self._jobs.values() if j.user_id == user_id and j.status == "pending")

    def get(self, job_id: str, user_id: str) -> Job | None:
        """Only the user who started a job can see it."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.user_id != user_id:
                return None
            return job

    def finish(self, job_id: str, result: dict[str, Any]) -> None:
        with self._lock:
            if job_id in self._jobs:
                self._jobs[job_id].status = "done"
                self._jobs[job_id].result = result

    def fail(self, job_id: str, error: str) -> None:
        with self._lock:
            if job_id in self._jobs:
                self._jobs[job_id].status = "error"
                self._jobs[job_id].error = error
