from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from .schemas import SAR, ActionName, ActionRecommendation, Route

STRONGLY_SUSPECTED_THRESHOLD = 0.70


class ResponseKind(str, Enum):
    confirmed = "confirmed"
    denied = "denied"
    authentication_succeeded = "authentication_succeeded"
    authentication_failed = "authentication_failed"
    no_response = "no_response"


def response_kind(value: str | None) -> ResponseKind | None:
    if value is None:
        return None
    # Free prose must not become testimony through a substring such as "authorized".
    aliases = {
        "customer denied the transaction": ResponseKind.denied,
        "customer reported that they did not make the transaction.": ResponseKind.denied,
        "customer confirmed the transaction": ResponseKind.confirmed,
    }
    normalized = value.strip().lower()
    if normalized in aliases:
        return aliases[normalized]
    try:
        return ResponseKind(normalized)
    except ValueError as exc:
        raise ValueError("Use a structured evidence response: " + ", ".join(ResponseKind)) from exc


@dataclass(frozen=True)
class PolicyContext:
    probability: float
    pattern: str
    exposure_usd: float
    trigger_type: str
    customer_response: str | None
    evidence_requested: bool
    shared_origin: bool
    confirmed_two_cards: bool = False
    recurring_legitimate: bool = False
    payment_status_known: bool = False
    payment_cleared: bool = False
    evidence_conflict: bool = False
    independent_supporting_groups: int = 0
    independent_legitimate_groups: int = 0
    confirmed_fraud: bool = False
    shared_origin_fraud: bool = False
    coordinated_abuse: bool = False
    customer_dispute: bool = False
    payment_pending: bool = False
    cleared_purchase_usd: float = 0
    hours_since_request: float = 0
    credentials_compromised: bool = False
    recommend_block_all: bool = False
    verdict: str | None = None

    def __post_init__(self) -> None:
        if not 0 <= self.probability <= 1 or self.exposure_usd < 0:
            raise ValueError("Invalid probability or exposure")
        if self.independent_supporting_groups < 0 or self.independent_legitimate_groups < 0:
            raise ValueError("Evidence group counts cannot be negative")
        if self.hours_since_request < 0 or self.cleared_purchase_usd < 0:
            raise ValueError("Elapsed time and cleared amount cannot be negative")
        if self.verdict not in {None, "fraud", "legitimate", "uncertain"}:
            raise ValueError("Invalid verdict")
        if self.payment_pending and self.payment_cleared:
            raise ValueError("Payment cannot be both pending and cleared")
        if (self.payment_pending or self.payment_cleared) and not self.payment_status_known:
            raise ValueError("Payment status must be known for status-dependent rules")
        response_kind(self.customer_response)


def _above(value: float, threshold: int) -> bool:
    return Decimal(str(value)) > threshold


def route_for(action: ActionName, exposure_usd: float) -> Route:
    if action == ActionName.DECLINE_TRANSACTION:
        return Route.L1
    if action == ActionName.BLOCK_CARD:
        return Route.L2 if _above(exposure_usd, 2500) else Route.L1
    if action in {ActionName.BLOCK_ALL_CARDS, ActionName.FILE_REPORT}:
        return Route.L2
    return Route.auto


def recommendation(action: ActionName, reason: str, exposure_usd: float) -> ActionRecommendation:
    return ActionRecommendation(action=action, route=route_for(action, exposure_usd), reason=reason)


def evaluate(ctx: PolicyContext) -> list[ActionRecommendation]:
    """Pure policy evaluation: all returned actions are recommendations only."""
    actions: dict[ActionName, ActionRecommendation] = {}

    def add(action: ActionName, reason: str) -> None:
        if action not in actions:
            actions[action] = recommendation(action, reason, ctx.exposure_usd)

    response = response_kind(ctx.customer_response)
    denied = response == ResponseKind.denied
    confirmed = response == ResponseKind.confirmed
    disputed = ctx.customer_dispute or ctx.trigger_type == "customer_report" or denied
    conflict = ctx.evidence_conflict or (confirmed and (
        ctx.confirmed_fraud or ctx.shared_origin_fraud or ctx.coordinated_abuse
    ))
    strong = ctx.probability >= STRONGLY_SUSPECTED_THRESHOLD and ctx.independent_supporting_groups >= 2
    qualified_fraud = ctx.confirmed_fraud or denied or strong or ctx.shared_origin_fraud or ctx.coordinated_abuse
    uncertain = ctx.verdict == "uncertain" or (
        ctx.verdict is None and not (confirmed or qualified_fraud or (
            ctx.probability <= 0.15 and ctx.independent_legitimate_groups >= 2
        ))
    )

    # Case creation is independent of whether another rule settles the alert.
    if ctx.probability >= 0.30 or ctx.evidence_requested or disputed:
        add(ActionName.CREATE_CASE, "§3a: probability ≥0.30, an evidence request, or a customer dispute requires a case")

    if ctx.recurring_legitimate and disputed:
        add(ActionName.VERIFY_WITH_CUSTOMER, "R7: verify the disputed recurring charge; merchant, amount, and monthly recurrence are established")
        add(ActionName.WARN_CUSTOMER, "R7: explain the recurring charge without blocking")
        if denied or conflict or (uncertain and _above(ctx.exposure_usd, 500)):
            add(ActionName.ESCALATE_TO_ANALYST, "R8: review the conflict between the dispute and established recurring activity")
        return list(actions.values())

    if confirmed and not conflict:
        add(ActionName.CLOSE_NO_FRAUD, "R3: customer verification confirms the flagged transaction")
        return list(actions.values())

    if denied:
        add(ActionName.BLOCK_CARD, "R2: customer verification denies the flagged transaction")

    if ctx.pattern == "card_testing":
        add(ActionName.DECLINE_TRANSACTION, "R5: at least three small online authorizations within one hour precede a larger purchase; decline is a recommendation")
        add(ActionName.STEP_UP_AUTH, "R5: request authentication for the established testing sequence")
        if ctx.payment_status_known and ctx.payment_cleared and _above(ctx.cleared_purchase_usd, 100):
            add(ActionName.BLOCK_CARD, "R5: an individual purchase over $100 is confirmed cleared")

    if not denied and ctx.probability < 0.70 and ctx.independent_supporting_groups <= 1:
        if ctx.probability <= 0.15 and ctx.independent_legitimate_groups >= 2 and not conflict:
            add(ActionName.CLOSE_NO_FRAUD, "§6: probability ≤0.15 and two independent legitimate evidence groups support closure")
        else:
            add(ActionName.VERIFY_WITH_CUSTOMER, "R1: verify the weak or single signal before any block")

    timed_out = ctx.evidence_requested and response == ResponseKind.no_response and ctx.hours_since_request >= 24
    if timed_out:
        add(ActionName.MONITOR_CARD, "R4: no response after 24 hours; keep the card under monitoring")
        if ctx.payment_status_known and ctx.payment_pending:
            add(ActionName.DECLINE_TRANSACTION, "R4: the authorization is confirmed pending after a 24-hour response timeout")
        if _above(ctx.exposure_usd, 500):
            add(ActionName.ESCALATE_TO_ANALYST, "R4: exposure exceeds $500 after the 24-hour timeout")

    if ctx.shared_origin_fraud:
        add(ActionName.CREATE_CASE, "R6: several cards show corroborated fraud from the same origin in one window")
        add(ActionName.MONITOR_CONNECTED_CARDS, "R6: monitor cards sharing the corroborated origin")

    if ctx.pattern == "undocumented" and ctx.coordinated_abuse:
        add(ActionName.CREATE_CASE, "R9: evidence establishes repeated or coordinated abuse across customers")
        add(ActionName.ESCALATE_TO_ANALYST, "R9: analyst review is required for the undocumented abuse pattern")

    reporting_condition = (
        _above(ctx.exposure_usd, 1000) or ctx.shared_origin_fraud or ctx.coordinated_abuse
    )
    # A generic profile match is a candidate link, not a qualified shared origin.
    if qualified_fraud and reporting_condition and not conflict:
        add(ActionName.CREATE_CASE, "§3a: every report has an internal case")
        add(ActionName.FILE_REPORT, "§3a, R2/R6/R9: confirmed or strongly suspected fraud meets a reporting condition; filing requires L2 approval")

    if conflict or (uncertain and _above(ctx.exposure_usd, 500)):
        add(ActionName.ESCALATE_TO_ANALYST, "R8: conflicting evidence or an uncertain verdict with exposure over $500 requires review")

    if ctx.recommend_block_all:
        if ctx.confirmed_two_cards or ctx.credentials_compromised:
            add(ActionName.BLOCK_ALL_CARDS, "R10: at least two of this customer's cards have confirmed fraud, or credentials are confirmed compromised")
        else:
            add(ActionName.ESCALATE_TO_ANALYST, "R10 and R8: requested all-card block lacks the required confirmed evidence")

    if not actions or set(actions) == {ActionName.CREATE_CASE}:
        add(ActionName.MONITOR_CARD, "§3b: retain monitoring while collecting evidence for the unresolved recommendation")
    return list(actions.values())


def next_actions(initial_context: PolicyContext, final_context: PolicyContext | None = None) -> tuple[list[ActionRecommendation], list[ActionRecommendation], str]:
    initial = evaluate(initial_context)
    final = evaluate(final_context) if final_context is not None else list(initial)
    if initial == final:
        return initial, final, "nothing"
    return initial, final, "The preserved initial recommendation was reassessed against the recorded evidence response and updated policy facts."


def sar_for(ctx: PolicyContext, *, customer_id: str, card_id: str, dates: list[str], subjects: list[str], pattern_description: str, amount: float) -> SAR:
    if not any(action.action == ActionName.FILE_REPORT for action in evaluate(ctx)):
        return SAR(file=False, reason="§3a: confirmed or strongly suspected fraud and a reporting condition were not both established without unresolved conflict")
    if len(dates) != 2 or dates[0] > dates[1]:
        raise ValueError("SAR activity dates must be ordered start and end dates")
    description = (pattern_description or ctx.pattern).strip().rstrip(".").replace(". ", "; ")
    narrative = (
        f"Activity involving customer {customer_id} and card {card_id} was identified between {dates[0]} and {dates[1]}. "
        f"The case identifies these subjects: {', '.join(subjects)}. "
        f"The observed activity is described as {description}. "
        f"The candidate episode has an estimated exposure of USD {amount:.2f}. "
        "Precise purchase locations, merchant identities, and settlement status are not established by the supplied source data. "
        f"The assessment assigns a fraud probability of {ctx.probability:.2f} based on the recorded evidence; customer responses, where simulated, remain assumptions. "
        "The assessed fraud and exposure or corroborated connections satisfy the reporting conditions in policy §3a. "
        "Filing is recommended for L2 review, and no regulatory report or banking control has been executed."
    )
    return SAR(
        file=True, reason="§3a: reporting is recommended under the applicable R2/R6/R9 conditions",
        narrative=narrative, subjects=subjects, total_amount_usd=amount, activity_dates=dates,
    )
