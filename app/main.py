from __future__ import annotations

import json

import httpx

from .config import settings
from .schemas import (
    ApprovalRequest,
    ApprovalResult,
    EvidenceSubmission,
    InvestigationRequest,
    InvestigationResponse,
)
from .service import InvestigationService
from .tigergraph import GraphToolError

try:
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import StreamingResponse
except ImportError:  # pragma: no cover - allows policy and CLI use without API extras
    FastAPI = None  # type: ignore[assignment,misc]


service = InvestigationService(settings)

if FastAPI is not None:
    app = FastAPI(title="HHGOA Fraud Investigation Agent", version="0.1.0")

    @app.get("/health")
    def health() -> dict[str, str | bool]:
        return {"status": "ok", "read_only": settings.read_only, "graph_backend": settings.graph_backend}

    @app.post("/api/v1/investigations", response_model=InvestigationResponse, status_code=201)
    def create_investigation(request: InvestigationRequest) -> InvestigationResponse:
        try:
            return service.investigate(request.case_id, request.evidence_response, request.idempotency_key, simulate_timeout=request.simulate_timeout)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except (GraphToolError, httpx.HTTPError) as exc:
            raise HTTPException(
                status_code=503,
                detail="TigerGraph is unavailable. Start the Savanna workspace, wait until it is running, and retry the investigation.",
            ) from exc

    @app.get("/api/v1/investigations/{case_id}", response_model=InvestigationResponse)
    def get_investigation(case_id: str) -> InvestigationResponse:
        result = service.get(case_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Investigation has not been run")
        return result

    @app.get("/api/v1/investigations")
    def list_investigations() -> list[dict]:
        return service.list_cases()

    @app.get("/api/v1/investigations/{case_id}/history", response_model=list[InvestigationResponse])
    def get_investigation_history(case_id: str) -> list[InvestigationResponse]:
        history = service.history(case_id)
        if not history:
            raise HTTPException(status_code=404, detail="Investigation has not been run")
        return history

    @app.post("/api/v1/investigations/{case_id}/evidence", response_model=InvestigationResponse)
    def submit_evidence(case_id: str, submission: EvidenceSubmission) -> InvestigationResponse:
        if service.get(case_id) is None:
            raise HTTPException(status_code=404, detail="Investigation has not been started")
        try:
            return service.investigate(case_id, submission.response, submission.idempotency_key, simulate_timeout=False)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except (GraphToolError, httpx.HTTPError) as exc:
            raise HTTPException(
                status_code=503,
                detail="TigerGraph is unavailable. Start the Savanna workspace, wait until it is running, and retry the evidence response.",
            ) from exc

    @app.post("/api/v1/investigations/{case_id}/approvals", response_model=ApprovalResult)
    def record_approval(case_id: str, request: ApprovalRequest) -> ApprovalResult:
        try:
            return service.approve(case_id, request)
        except ValueError as exc:
            status = 409 if "stale" in str(exc) else 400
            raise HTTPException(status_code=status, detail=str(exc)) from exc

    @app.get("/api/v1/investigations/{case_id}/approvals", response_model=list[ApprovalResult])
    def list_approvals(case_id: str) -> list[ApprovalResult]:
        if service.get(case_id) is None:
            raise HTTPException(status_code=404, detail="Investigation has not been started")
        return service.approvals(case_id)

    @app.get("/api/v1/investigations/{case_id}/events")
    def investigation_events(case_id: str) -> StreamingResponse:
        history = service.history(case_id)
        if not history:
            raise HTTPException(status_code=404, detail="Investigation has not been started")

        def stream():
            for item in history:
                yield f"event: revision\ndata: {json.dumps(item.model_dump(mode='json'))}\n\n"

        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})
else:
    app = None
