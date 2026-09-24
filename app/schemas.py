from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ActionName(str, Enum):
    ALLOW_TRANSACTION = "ALLOW_TRANSACTION"
    DECLINE_TRANSACTION = "DECLINE_TRANSACTION"
    MONITOR_CARD = "MONITOR_CARD"
    MONITOR_CONNECTED_CARDS = "MONITOR_CONNECTED_CARDS"
    WARN_CUSTOMER = "WARN_CUSTOMER"
    VERIFY_WITH_CUSTOMER = "VERIFY_WITH_CUSTOMER"
    STEP_UP_AUTH = "STEP_UP_AUTH"
    BLOCK_CARD = "BLOCK_CARD"
    BLOCK_ALL_CARDS = "BLOCK_ALL_CARDS"
    GENERATE_REPORT = "GENERATE_REPORT"
    CREATE_CASE = "CREATE_CASE"
    FILE_REPORT = "FILE_REPORT"
    ESCALATE_TO_ANALYST = "ESCALATE_TO_ANALYST"
    CLOSE_NO_FRAUD = "CLOSE_NO_FRAUD"


class Route(str, Enum):
    auto = "auto"
    L1 = "L1"
    L2 = "L2"


class Pattern(str, Enum):
    card_testing = "card_testing"
    card_not_present_fraud = "card_not_present_fraud"
    card_not_present_new_device = "card_not_present_new_device"
    out_of_region_use = "out_of_region_use"
    account_takeover = "account_takeover"
    undocumented = "undocumented"
    none = "none"


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    claim: str
    source: Literal["graph", "document", "customer", "external"]
    ref: str
    entity_ids: list[str] = Field(default_factory=list)
    independent_group: str | None = None
    simulated: bool = False
    direction: Literal["supports", "contradicts", "neutral"] = "neutral"


class ActionRecommendation(BaseModel):
    action: ActionName
    route: Route
    reason: str


class CaseRecord(BaseModel):
    status: Literal["open", "closed_fraud", "closed_legitimate", "escalated"]
    verdict: Literal["fraud", "legitimate", "uncertain"]
    fraud_probability: float = Field(ge=0, le=1)
    pattern: Pattern
    pattern_description: str = ""
    affected_txn_ids: list[str] = Field(default_factory=list)
    first_suspicious_txn_id: str = ""
    connected_card_ids: list[str] = Field(default_factory=list)
    connected_device_profiles: list[str] = Field(default_factory=list)
    exposure_usd: float = Field(ge=0)
    evidence: list[Evidence] = Field(default_factory=list)
    similar_prior_cases: list[str] = Field(default_factory=list)
    summary: str
    written_to_graph: bool = False
    graph_case_id: str = ""


class EvidenceRequest(BaseModel):
    type: Literal["customer_validation", "step_up_auth", "analyst_info"]
    asked_after_step: int = Field(ge=0)
    assumed_response: str
    simulated: bool = True


class NextBestActions(BaseModel):
    initial: list[ActionRecommendation] = Field(default_factory=list)
    final: list[ActionRecommendation] = Field(default_factory=list)
    what_changed: str = "nothing"


class SAR(BaseModel):
    file: bool
    reason: str
    narrative: str = ""
    subjects: list[str] = Field(default_factory=list)
    total_amount_usd: float = 0
    activity_dates: list[str] = Field(default_factory=list)


class AnswerFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: str
    case: CaseRecord
    evidence_requests: list[EvidenceRequest] = Field(default_factory=list)
    next_best_actions: NextBestActions
    sar: SAR
    stop_reason: str
    tool_calls: int = Field(ge=0)
    tokens: int = Field(ge=0)
    latency_s: float = Field(ge=0)

    def submission_dict(self) -> dict:
        return self.model_dump(mode="json", exclude={
            "case": {"evidence": {"__all__": {"independent_group", "simulated", "direction"}}},
            "evidence_requests": {"__all__": {"simulated"}},
        })


class InvestigationRequest(BaseModel):
    case_id: str
    evidence_response: str | None = None
    idempotency_key: str | None = None
    # Interactive analyst runs pause with a pending request. Benchmark exports
    # may opt into the documented unattended 24-hour no-response simulation.
    simulate_timeout: bool = False


class EvidenceSubmission(BaseModel):
    response: str
    idempotency_key: str | None = None


class ApprovalRequest(BaseModel):
    action: ActionName
    revision: int = Field(ge=1)
    role: Literal["L1", "L2"]
    decision: Literal["approved", "rejected"]
    idempotency_key: str | None = None


class ApprovalResult(BaseModel):
    approval_id: str
    case_id: str
    action: ActionName
    revision: int
    role: Literal["L1", "L2"]
    decision: Literal["approved", "rejected"]
    execution_status: Literal["simulated_only"] = "simulated_only"
    created_at: datetime


class InvestigationResponse(BaseModel):
    investigation_id: str
    answer: AnswerFile
    created_at: datetime
    revision: int = 1
    phase: Literal["awaiting_evidence", "completed"] = "completed"
