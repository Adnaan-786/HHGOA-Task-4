from dataclasses import replace

import pytest

from app.policy import (
    PolicyContext,
    ResponseKind,
    evaluate,
    next_actions,
    response_kind,
    route_for,
    sar_for,
)
from app.schemas import ActionName as A
from app.schemas import Route


def context(**changes):
    base = PolicyContext(
        probability=0.45, pattern="none", exposure_usd=90,
        trigger_type="risk_score", customer_response=None,
        evidence_requested=False, shared_origin=False,
    )
    return replace(base, **changes)


def actions(ctx):
    return {item.action for item in evaluate(ctx)}


@pytest.mark.parametrize("amount,route", [(2499.99, Route.L1), (2500, Route.L1), (2500.01, Route.L2)])
def test_block_route(amount, route):
    assert route_for(A.BLOCK_CARD, amount) == route


@pytest.mark.parametrize("action,route", [(A.DECLINE_TRANSACTION, Route.L1), (A.BLOCK_ALL_CARDS, Route.L2), (A.FILE_REPORT, Route.L2)])
def test_always_routed(action, route):
    assert route_for(action, 0) == route


@pytest.mark.parametrize("p,expected", [(0.2999, False), (0.30, True), (0.3001, True)])
def test_case_threshold(p, expected):
    assert (A.CREATE_CASE in actions(context(probability=p))) is expected


@pytest.mark.parametrize("changes", [
    {"evidence_requested": True}, {"customer_dispute": True}, {"trigger_type": "customer_report"},
])
def test_case_creation_independent_of_probability(changes):
    assert A.CREATE_CASE in actions(context(probability=0.01, **changes))


@pytest.mark.parametrize("p", [0.0, 0.6999])
def test_r1_weak_single_signal(p):
    result = actions(context(probability=p, independent_supporting_groups=1))
    assert A.VERIFY_WITH_CUSTOMER in result
    assert A.BLOCK_CARD not in result


@pytest.mark.parametrize("p,strong", [(0.6999, False), (0.70, True), (0.7001, True)])
def test_strong_suspicion_boundary(p, strong):
    assert (A.FILE_REPORT in actions(context(probability=p, independent_supporting_groups=2, exposure_usd=1000.01))) is strong


def test_correlated_signals_do_not_establish_strong_suspicion():
    assert A.FILE_REPORT not in actions(context(probability=0.99, independent_supporting_groups=1, exposure_usd=2000))


@pytest.mark.parametrize("amount,report", [(999.99, False), (1000, False), (1000.01, True)])
def test_r2_reporting_boundary(amount, report):
    result = actions(context(customer_response="denied", exposure_usd=amount))
    assert {A.BLOCK_CARD, A.CREATE_CASE} <= result
    assert (A.FILE_REPORT in result) is report


def test_r3_confirmation_settles_without_reporting():
    result = actions(context(customer_response="confirmed", evidence_requested=True, exposure_usd=2000))
    assert result == {A.CREATE_CASE, A.CLOSE_NO_FRAUD}


@pytest.mark.parametrize("phrase", ["unauthorized", "not confirmed", "I did not authorize this", "confirmation unavailable"])
def test_prose_is_not_interpreted_as_confirmation(phrase):
    with pytest.raises(ValueError, match="structured evidence"):
        response_kind(phrase)


@pytest.mark.parametrize("response", ["authentication_succeeded", "authentication_failed"])
def test_authentication_is_not_customer_testimony(response):
    result = actions(context(customer_response=response))
    assert A.BLOCK_CARD not in result
    assert A.CLOSE_NO_FRAUD not in result


@pytest.mark.parametrize("hours,timeout", [(23.99, False), (24, True), (24.01, True)])
def test_r4_waits_full_day(hours, timeout):
    result = actions(context(evidence_requested=True, customer_response="no_response", hours_since_request=hours))
    assert (A.MONITOR_CARD in result) is timeout


@pytest.mark.parametrize("amount,escalated", [(499.99, False), (500, False), (500.01, True)])
def test_r4_exposure_boundary(amount, escalated):
    result = actions(context(
        probability=0.9, independent_supporting_groups=2, verdict="fraud",
        evidence_requested=True, customer_response="no_response", hours_since_request=24, exposure_usd=amount,
    ))
    assert (A.ESCALATE_TO_ANALYST in result) is escalated


@pytest.mark.parametrize("known,pending,decline", [(False, False, False), (True, False, False), (True, True, True)])
def test_r4_declines_only_known_pending_authorization(known, pending, decline):
    result = actions(context(
        evidence_requested=True, customer_response="no_response", hours_since_request=24,
        payment_status_known=known, payment_pending=pending,
    ))
    assert (A.DECLINE_TRANSACTION in result) is decline


@pytest.mark.parametrize("purchase,blocked", [(99.99, False), (100, False), (100.01, True)])
def test_r5_uses_individual_cleared_purchase(purchase, blocked):
    result = actions(context(
        pattern="card_testing", exposure_usd=1200, payment_status_known=True,
        payment_cleared=True, cleared_purchase_usd=purchase,
    ))
    assert {A.DECLINE_TRANSACTION, A.STEP_UP_AUTH} <= result
    assert (A.BLOCK_CARD in result) is blocked


def test_r5_unknown_status_cannot_block():
    assert A.BLOCK_CARD not in actions(context(pattern="card_testing", exposure_usd=5000))


def test_r6_common_profile_is_not_a_fraud_ring():
    result = actions(context(shared_origin=True))
    assert A.FILE_REPORT not in result
    assert A.MONITOR_CONNECTED_CARDS not in result


def test_r6_corroborated_shared_fraud():
    result = actions(context(shared_origin=True, shared_origin_fraud=True))
    assert {A.CREATE_CASE, A.FILE_REPORT, A.MONITOR_CONNECTED_CARDS} <= result


def test_r7_takes_precedence_over_denial_and_flags_conflict():
    result = actions(context(customer_response="denied", recurring_legitimate=True, exposure_usd=2000))
    assert {A.CREATE_CASE, A.VERIFY_WITH_CUSTOMER, A.WARN_CUSTOMER, A.ESCALATE_TO_ANALYST} <= result
    assert A.BLOCK_CARD not in result
    assert A.FILE_REPORT not in result


@pytest.mark.parametrize("amount,escalated", [(499.99, False), (500, False), (500.01, True)])
def test_r8_uncertain_exposure_boundary(amount, escalated):
    assert (A.ESCALATE_TO_ANALYST in actions(context(exposure_usd=amount, verdict="uncertain"))) is escalated


def test_conflicting_confirmation_does_not_close_or_file():
    result = actions(context(customer_response="confirmed", confirmed_fraud=True, exposure_usd=2000))
    assert A.ESCALATE_TO_ANALYST in result
    assert A.CLOSE_NO_FRAUD not in result
    assert A.FILE_REPORT not in result


def test_r9_requires_evidence_of_coordinated_abuse():
    assert A.FILE_REPORT not in actions(context(pattern="undocumented"))
    assert {A.CREATE_CASE, A.FILE_REPORT, A.ESCALATE_TO_ANALYST} <= actions(context(pattern="undocumented", coordinated_abuse=True))


@pytest.mark.parametrize("two_cards,credentials,allowed", [(False, False, False), (True, False, True), (False, True, True)])
def test_r10(two_cards, credentials, allowed):
    result = actions(context(recommend_block_all=True, confirmed_two_cards=two_cards, credentials_compromised=credentials))
    assert (A.BLOCK_ALL_CARDS in result) is allowed


@pytest.mark.parametrize("p,closed", [(0.1499, True), (0.15, True), (0.1501, False)])
def test_low_probability_closure_requires_independent_evidence(p, closed):
    assert (A.CLOSE_NO_FRAUD in actions(context(probability=p, independent_legitimate_groups=2))) is closed
    assert A.CLOSE_NO_FRAUD not in actions(context(probability=p, independent_legitimate_groups=1))


def test_initial_recommendation_survives_denial():
    before = context(evidence_requested=True)
    after = replace(before, customer_response=ResponseKind.denied, probability=0.9)
    initial, final, changed = next_actions(before, after)
    assert A.VERIFY_WITH_CUSTOMER in {a.action for a in initial}
    assert A.BLOCK_CARD not in {a.action for a in initial}
    assert A.BLOCK_CARD in {a.action for a in final}
    assert changed != "nothing"
    assert next_actions(before)[0] == next_actions(before)[1]


def test_sar_and_actions_agree_without_claiming_execution():
    ctx = context(customer_response="denied", exposure_usd=1000.01)
    sar = sar_for(ctx, customer_id="C1", card_id="C1-K1", dates=["2016-11-01", "2016-11-02"],
                  subjects=["C1", "C1-K1"], pattern_description="online purchases", amount=1000.01)
    assert sar.file == (A.FILE_REPORT in actions(ctx))
    assert "no regulatory report or banking control has been executed" in sar.narrative
    assert sar.activity_dates == ["2016-11-01", "2016-11-02"]


def test_unknown_payment_status_rejects_cleared_flag():
    with pytest.raises(ValueError, match="status must be known"):
        context(payment_cleared=True)
