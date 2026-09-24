from dataclasses import replace
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app.calibration import CalibratedScorer, estimate_probability, sigmoid
from app.evaluation import historical_trigger, split_for
from app.features import FEATURE_VERSION, analyze_behavior
from app.repository import DatasetRepository, Identity, Transaction


def sample_repository(tmp_path):
    class LabelBlindRepository(DatasetRepository):
        @property
        def history(self):
            raise AssertionError("Feature extraction must not read historical labels")

    repo = LabelBlindRepository(tmp_path)
    now = datetime.fromisoformat("2016-11-12T12:00:00")
    tx = Transaction("target", "C1", "C1-K1", 50, "W", "online", now, .9, "home", "", "example.com", "")
    prior = [replace(tx, transaction_id=f"old-{i}", timestamp=now - timedelta(days=10 + i)) for i in range(5)]
    repo._transactions = {t.transaction_id: t for t in [*prior, tx]}
    repo._by_card = {tx.card_id: [*reversed(prior), tx]}
    repo._identities = {}
    return repo, tx


def artifact():
    return {
        "feature_version": FEATURE_VERSION, "feature_names": ["x"],
        "mean": [2.0], "scale": [4.0], "coefficients": [3.0], "intercept": -.5,
        "calibration_slope": .8, "calibration_intercept": -.2,
        "available_at": "2016-10-01T00:00:00",
    }


def test_feature_extraction_cannot_read_outcomes(tmp_path):
    repo, tx = sample_repository(tmp_path)
    result = analyze_behavior(repo, tx.transaction_id, tx.timestamp)
    assert result.pattern == "none"
    assert result.features["baseline_available"] == 1
    assert "transaction_behavior" in result.legitimate_groups


def test_future_transactions_and_identity_do_not_change_features(tmp_path):
    repo, tx = sample_repository(tmp_path)
    before = analyze_behavior(repo, tx.transaction_id, tx.timestamp)
    future = replace(tx, transaction_id="future", amount_usd=1000000, timestamp=tx.timestamp + timedelta(seconds=1))
    repo._transactions[future.transaction_id] = future
    repo._by_card[tx.card_id].append(future)
    repo._identities[future.transaction_id] = Identity("future", "new profile", "", "", "New", "proxy", "", "", "", "")
    assert analyze_behavior(repo, tx.transaction_id, tx.timestamp) == before


def test_flagged_transaction_cannot_create_its_own_region_baseline(tmp_path):
    repo, tx = sample_repository(tmp_path)
    changed = replace(tx, addr1="away", channel="in_person")
    repo._transactions[tx.transaction_id] = changed
    repo._by_card[tx.card_id][-1] = changed
    result = analyze_behavior(repo, tx.transaction_id, tx.timestamp)
    assert result.features["region_novel"] == 1
    assert result.pattern != "out_of_region_use"  # Novel region alone does not establish cloning.


def test_missing_identity_does_not_create_fraud_evidence(tmp_path):
    repo, tx = sample_repository(tmp_path)
    result = analyze_behavior(repo, tx.transaction_id, tx.timestamp)
    assert "online identity record" in result.missing
    assert result.features["device_marked_new"] == 0
    assert "device_identity" not in result.supporting_groups


def test_new_phone_alone_is_not_a_fraud_pattern(tmp_path):
    repo, tx = sample_repository(tmp_path)
    repo._identities[tx.transaction_id] = Identity(tx.transaction_id, "new phone", "mobile", "phone", "New", "", "OS", "browser", "screen", "")
    result = analyze_behavior(repo, tx.transaction_id, tx.timestamp)
    assert result.pattern == "none"
    assert "device_identity" not in result.supporting_groups


def test_future_flagged_transaction_is_rejected(tmp_path):
    repo, tx = sample_repository(tmp_path)
    with pytest.raises(ValueError, match="later than"):
        analyze_behavior(repo, tx.transaction_id, tx.timestamp - timedelta(seconds=1))


def test_calibration_portable_numeric_prediction():
    scorer = CalibratedScorer(artifact())
    result = scorer.estimate({"x": 6.0}, datetime.fromisoformat("2016-11-01"))
    assert result.probability == pytest.approx(sigmoid(.8 * (-.5 + 3 * (6 - 2) / 4) - .2))
    assert result.available
    assert "not all bank transactions" in result.population


def test_calibration_cannot_use_future_outcomes():
    with pytest.raises(ValueError, match="unavailable"):
        CalibratedScorer(artifact()).estimate({"x": 6.0}, datetime.fromisoformat("2016-09-30"))


@pytest.mark.parametrize("features", [{"wrong_name": 1}, {"x": float("nan")}, {"x": 1, "future_label": 1}])
def test_calibration_feature_contract(features):
    with pytest.raises(ValueError, match="Features do not match"):
        CalibratedScorer(artifact()).estimate(features, datetime.fromisoformat("2016-11-01"))


def test_missing_model_does_not_copy_risk_score():
    for risk in (.01, .99):
        estimate = estimate_probability({"risk_score": risk}, datetime.fromisoformat("2016-11-01"), None)
        assert estimate.probability == .5
        assert not estimate.available


@pytest.mark.parametrize("opened,closed,expected", [
    ("2016-08-25", "2016-08-31", "train"),
    ("2016-08-31", "2016-09-01", "purged"),
    ("2016-09-01", "2016-09-30", "calibration"),
    ("2016-09-30", "2016-10-01", "purged"),
    ("2016-10-01", "2016-10-02", "holdout"),
])
def test_partitions_use_actual_outcome_availability(opened, closed, expected):
    case = SimpleNamespace(opened_at=datetime.fromisoformat(opened), closed_at=datetime.fromisoformat(closed))
    assert split_for(case, datetime.fromisoformat("2016-09-01"), datetime.fromisoformat("2016-10-01")) == expected


def test_historical_trigger_uses_one_documented_transaction(tmp_path):
    repo, tx = sample_repository(tmp_path)
    unrelated = replace(tx, transaction_id="unrelated", timestamp=tx.timestamp + timedelta(seconds=1))
    repo._transactions[unrelated.transaction_id] = unrelated
    repo._by_card[tx.card_id].append(unrelated)
    case = SimpleNamespace(card_id=tx.card_id, opened_at=unrelated.timestamp, txn_ids=(tx.transaction_id, "old-0"))
    assert historical_trigger(repo, case) == tx.transaction_id
