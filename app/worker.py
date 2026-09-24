from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Lock

from .answer import validate_answer
from .investigator import Investigator
from .repository import DatasetRepository
from .schemas import AnswerFile


@dataclass
class Job:
    case_id: str
    status: str = "queued"
    attempts: int = 0
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None


class InvestigationWorker:
    """Execution boundary shared by the local worker and durable service.

    The worker owns investigation execution while the API owns validation. The
    service persists immutable revisions and idempotency keys when PostgreSQL is
    configured; graph projection remains idempotent on retries.
    """

    def __init__(self, investigator: Investigator, repository: DatasetRepository):
        self.investigator = investigator
        self.repository = repository
        self._lock = Lock()
        self.jobs: dict[str, Job] = {}

    def run(self, case_id: str, evidence_response: str | None = None, *, simulation: bool = True, revision: int = 1) -> AnswerFile:
        with self._lock:
            job = self.jobs.setdefault(case_id, Job(case_id=case_id))
            job.attempts += 1
            job.status = "running"
            job.started_at = datetime.now(UTC)
        try:
            answer = self.investigator.run(case_id, evidence_response, simulation=simulation, revision=revision)
            validate_answer(answer, self.repository)
            with self._lock:
                job.status = "completed"
                job.completed_at = datetime.now(UTC)
            return answer
        except Exception as exc:
            with self._lock:
                job.status = "failed"
                job.error = str(exc)
            raise
