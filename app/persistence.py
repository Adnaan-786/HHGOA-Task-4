from __future__ import annotations

from typing import Any

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    create_engine,
    select,
)

from .schemas import ApprovalResult, InvestigationResponse


class DurableCaseStore:
    """Small durable revision store used by the API service.

    The answer snapshot is deliberately immutable. A new evidence response gets
    a new revision, while the idempotency key points to exactly one snapshot.
    PostgreSQL is the supported deployment database; a SQLite URL is useful only
    for isolated unit tests of this adapter.
    """

    def __init__(self, database_url: str):
        self.engine = create_engine(database_url, pool_pre_ping=True)
        self.metadata = MetaData()
        self.revisions = Table(
            "case_revisions", self.metadata,
            # A string identifier avoids coupling the answer contract to a UUID
            # implementation and keeps imported historical IDs inspectable.
            Column("investigation_id", String(64), primary_key=True),
            Column("case_id", String(128), nullable=False, index=True),
            Column("revision", Integer, nullable=False),
            Column("idempotency_key", String(128), nullable=False, unique=True),
            Column("request_hash", String(64), nullable=False),
            Column("phase", String(32), nullable=False),
            Column("created_at", DateTime(timezone=True), nullable=False),
            Column("answer", JSON, nullable=False),
            UniqueConstraint("case_id", "revision", name="case_revisions_case_revision_key"),
        )
        self.approvals = Table(
            "case_approvals", self.metadata,
            Column("approval_id", String(64), primary_key=True),
            Column("case_id", String(128), nullable=False, index=True),
            Column("action", String(64), nullable=False),
            Column("revision", Integer, nullable=False),
            Column("role", String(8), nullable=False),
            Column("decision", String(16), nullable=False),
            Column("idempotency_key", String(128), nullable=False, unique=True),
            Column("created_at", DateTime(timezone=True), nullable=False),
        )
        self.metadata.create_all(self.engine)

    def by_key(self, key: str) -> InvestigationResponse | None:
        with self.engine.connect() as connection:
            row = connection.execute(select(self.revisions.c.answer).where(self.revisions.c.idempotency_key == key)).first()
        return InvestigationResponse.model_validate(row.answer) if row else None

    def latest(self, case_id: str) -> InvestigationResponse | None:
        with self.engine.connect() as connection:
            row = connection.execute(
                select(self.revisions.c.answer).where(self.revisions.c.case_id == case_id).order_by(self.revisions.c.revision.desc()).limit(1)
            ).first()
        return InvestigationResponse.model_validate(row.answer) if row else None

    def history(self, case_id: str) -> list[InvestigationResponse]:
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(self.revisions.c.answer).where(self.revisions.c.case_id == case_id).order_by(self.revisions.c.revision.asc())
            ).all()
        return [InvestigationResponse.model_validate(row.answer) for row in rows]

    def save(self, *, key: str, request_hash: str, response: InvestigationResponse) -> None:
        values: dict[str, Any] = {
            "investigation_id": response.investigation_id, "case_id": response.answer.case_id,
            "revision": response.revision, "idempotency_key": key, "request_hash": request_hash,
            "phase": response.phase, "created_at": response.created_at, "answer": response.model_dump(mode="json"),
        }
        with self.engine.begin() as connection:
            connection.execute(self.revisions.insert().values(**values))

    def approval_by_key(self, key: str) -> ApprovalResult | None:
        with self.engine.connect() as connection:
            row = connection.execute(select(self.approvals).where(self.approvals.c.idempotency_key == key)).first()
        return ApprovalResult.model_validate(dict(row._mapping)) if row else None

    def save_approval(self, *, key: str, result: ApprovalResult) -> None:
        with self.engine.begin() as connection:
            connection.execute(self.approvals.insert().values(
                approval_id=result.approval_id, case_id=result.case_id, action=result.action.value,
                revision=result.revision, role=result.role, decision=result.decision,
                idempotency_key=key, created_at=result.created_at,
            ))

    def approvals_for(self, case_id: str) -> list[ApprovalResult]:
        with self.engine.connect() as connection:
            rows = connection.execute(select(self.approvals).where(self.approvals.c.case_id == case_id).order_by(self.approvals.c.created_at.asc())).all()
        return [ApprovalResult.model_validate(dict(row._mapping)) for row in rows]
