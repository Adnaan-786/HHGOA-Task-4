from __future__ import annotations

import json
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    balanced_accuracy_score,
    brier_score_loss,
    log_loss,
    roc_auc_score,
)
from sklearn.preprocessing import StandardScaler

from .calibration import CalibratedScorer
from .features import FEATURE_VERSION, analyze_behavior
from .ingest import sha256
from .repository import DatasetRepository, HistoricalCase


def split_for(case: HistoricalCase, train_end: datetime, calibration_end: datetime) -> str:
    if case.opened_at < train_end:
        return "train" if case.closed_at < train_end else "purged"
    if case.opened_at < calibration_end:
        return "calibration" if case.closed_at < calibration_end else "purged"
    return "holdout"


def historical_trigger(repository: DatasetRepository, case: HistoricalCase) -> str | None:
    # Only this one surrogate trigger crosses into the label-blind feature extractor.
    # Using the latest transaction on the entire card mismatches many case labels.
    candidates = [repository.transactions[tid] for tid in case.txn_ids
                  if tid in repository.transactions and repository.transactions[tid].timestamp <= case.opened_at]
    if not candidates:
        return None
    return max(candidates, key=lambda tx: (tx.timestamp, tx.transaction_id)).transaction_id


def calibration_bins(labels: list[int], probabilities: list[float]) -> list[dict]:
    bins = []
    for index in range(10):
        selected = [(y, p) for y, p in zip(labels, probabilities, strict=True)
                    if min(int(p * 10), 9) == index]
        if selected:
            bins.append({"lower": index / 10, "upper": (index + 1) / 10, "count": len(selected),
                         "mean_probability": sum(p for _, p in selected) / len(selected),
                         "observed_fraud_rate": sum(y for y, _ in selected) / len(selected)})
    return bins


def metrics(labels: list[int], probabilities: list[float]) -> dict:
    predictions = [int(p >= 0.5) for p in probabilities]
    bins = calibration_bins(labels, probabilities)
    return {
        "count": len(labels), "fraud_count": sum(labels), "cleared_count": len(labels) - sum(labels),
        "brier": float(brier_score_loss(labels, probabilities)),
        "log_loss": float(log_loss(labels, probabilities, labels=[0, 1])),
        "roc_auc": float(roc_auc_score(labels, probabilities)) if len(set(labels)) > 1 else None,
        "balanced_accuracy_at_0_5": float(balanced_accuracy_score(labels, predictions)),
        "expected_calibration_error": sum(b["count"] * abs(b["mean_probability"] - b["observed_fraud_rate"]) for b in bins) / len(labels),
        "confusion_at_0_5": {"true_positive": sum(y == 1 and p == 1 for y, p in zip(labels, predictions)),
                             "false_positive": sum(y == 0 and p == 1 for y, p in zip(labels, predictions)),
                             "true_negative": sum(y == 0 and p == 0 for y, p in zip(labels, predictions)),
                             "false_negative": sum(y == 1 and p == 0 for y, p in zip(labels, predictions))},
        "calibration_bins": bins,
    }


def evaluate_history(root: Path, output: Path) -> dict:
    started = time.perf_counter()
    repository = DatasetRepository(root)
    # These dates, features, and fixed hyperparameters are specified before holdout scoring.
    train_end = datetime.fromisoformat("2016-09-01T00:00:00")
    calibration_end = datetime.fromisoformat("2016-10-01T00:00:00")
    benchmark_start = min(case.opened_at for case in repository.benchmarks.values())
    rows = []
    excluded = []
    for case in sorted(repository.history, key=lambda item: (item.opened_at, item.case_id)):
        split = split_for(case, train_end, calibration_end)
        if split == "purged" or case.closed_at >= benchmark_start:
            excluded.append({"case_id": case.case_id, "reason": "outcome unavailable at partition boundary"})
            continue
        trigger = historical_trigger(repository, case)
        if trigger is None:
            excluded.append({"case_id": case.case_id, "reason": "no transaction available at opening"})
            continue
        behavior = analyze_behavior(repository, trigger, case.opened_at)
        rows.append({"case": case, "trigger": trigger, "split": split, "behavior": behavior,
                     "label": int(case.outcome == "confirmed_fraud")})
    grouped = {split: [row for row in rows if row["split"] == split] for split in ("train", "calibration", "holdout")}
    if any(len(group) < 100 or len({r["label"] for r in group}) < 2 for group in grouped.values()):
        raise ValueError("Each chronological partition needs at least 100 cases and both outcomes")
    names = sorted(rows[0]["behavior"].features)

    def matrix(group):
        return np.asarray([[r["behavior"].features[name] for name in names] for r in group])

    scaler = StandardScaler().fit(matrix(grouped["train"]))
    model = LogisticRegression(C=1.0, max_iter=2000, random_state=0).fit(
        scaler.transform(matrix(grouped["train"])), [r["label"] for r in grouped["train"]],
    )
    calibration_scores = model.decision_function(scaler.transform(matrix(grouped["calibration"]))).reshape(-1, 1)
    calibrator = LogisticRegression(C=1.0, max_iter=2000, random_state=0).fit(
        calibration_scores, [r["label"] for r in grouped["calibration"]],
    )
    artifact = {
        "feature_version": FEATURE_VERSION, "feature_names": names,
        "mean": scaler.mean_.tolist(), "scale": scaler.scale_.tolist(),
        "coefficients": model.coef_[0].tolist(), "intercept": float(model.intercept_[0]),
        "calibration_slope": float(calibrator.coef_[0, 0]), "calibration_intercept": float(calibrator.intercept_[0]),
        "available_at": calibration_end.isoformat(),
        "training_case_ids": [r["case"].case_id for r in grouped["train"]],
        "calibration_case_ids": [r["case"].case_id for r in grouped["calibration"]],
        "max_training_outcome_available_at": max(r["case"].closed_at for r in grouped["train"]).isoformat(),
        "max_calibration_outcome_available_at": max(r["case"].closed_at for r in grouped["calibration"]).isoformat(),
        "source_sha256": {name: sha256(root / name) for name in ("transactions.csv", "identity.csv", "closed_cases_history.csv")},
        "feature_code_sha256": sha256(Path(__file__).with_name("features.py")),
        "method": "standardized logistic regression; Platt calibration on a later partition; no class-balance adjustment",
        "population": "selected historical investigations",
    }
    scorer = CalibratedScorer(artifact)
    holdout = grouped["holdout"]
    probabilities = [scorer.estimate(r["behavior"].features, r["case"].opened_at).probability for r in holdout]
    reference = calibrator.predict_proba(model.decision_function(scaler.transform(matrix(holdout))).reshape(-1, 1))[:, 1]
    if not np.allclose(probabilities, reference, rtol=1e-12, atol=1e-12):
        raise ValueError("Portable scorer differs from the fitted estimator")
    labels = [r["label"] for r in holdout]
    prior = sum(r["label"] for r in grouped["calibration"]) / len(grouped["calibration"])
    predictions = []
    for row, probability in zip(holdout, probabilities, strict=True):
        case, behavior = row["case"], row["behavior"]
        predicted_episode = set(behavior.episode_ids) if probability > 0.15 else set()
        actual_episode = set(case.txn_ids) if row["label"] else set()
        intersection = len(predicted_episode & actual_episode)
        union = len(predicted_episode | actual_episode)
        predictions.append({
            "case_id": case.case_id, "opened_at": case.opened_at.isoformat(), "trigger_id": row["trigger"],
            "actual_fraud": bool(row["label"]), "probability": probability,
            "predicted_verdict": "fraud" if probability >= .70 else "legitimate" if probability <= .15 else "uncertain",
            "actual_pattern": case.pattern, "predicted_pattern": behavior.pattern if probability > .15 else "none",
            "episode_precision": intersection / len(predicted_episode) if predicted_episode else float(not actual_episode),
            "episode_recall": intersection / len(actual_episode) if actual_episode else float(not predicted_episode),
            "episode_jaccard": intersection / union if union else 1.0,
            "exposure_error_usd": abs(sum(abs(repository.transactions[tid].amount_cents) for tid in predicted_episode) / 100 - case.exposure_usd),
            "trigger_in_documented_case": row["trigger"] in case.txn_ids,
        })
    report = {
        "model_id": scorer.model_id,
        "partitions": {split: {"count": len(group), "fraud": sum(r["label"] for r in group),
                                "first_opened_at": min(r["case"].opened_at for r in group).isoformat(),
                                "last_opened_at": max(r["case"].opened_at for r in group).isoformat()}
                       for split, group in grouped.items()},
        "purged": excluded,
        "holdout": metrics(labels, probabilities),
        "raw_risk_score_baseline": metrics(labels, [r["behavior"].features["risk_score"] for r in holdout]),
        "constant_calibration_prior_baseline": metrics(labels, [prior] * len(holdout)),
        "pattern_accuracy": sum(p["actual_pattern"] == p["predicted_pattern"] for p in predictions) / len(predictions),
        "pattern_confusion": dict(Counter(p["actual_pattern"] + " -> " + p["predicted_pattern"] for p in predictions)),
        "mean_episode_precision": sum(p["episode_precision"] for p in predictions) / len(predictions),
        "mean_episode_recall": sum(p["episode_recall"] for p in predictions) / len(predictions),
        "mean_episode_jaccard": sum(p["episode_jaccard"] for p in predictions) / len(predictions),
        "mean_absolute_exposure_error_usd": sum(p["exposure_error_usd"] for p in predictions) / len(predictions),
        "surrogate_trigger_in_documented_case_rate": sum(p["trigger_in_documented_case"] for p in predictions) / len(predictions),
        "verdict_counts": dict(Counter(p["predicted_verdict"] for p in predictions)),
        "latency_s": time.perf_counter() - started,
        "limitations": [
            "Selected closed cases are not representative of all transactions or the benchmark class distribution.",
            "Historical triggers are reconstructed as the latest transaction referenced by the case and available at opening; no flagged ID was supplied. Other gold episode IDs never reach the investigator.",
            "Historical patterns may depend on unavailable verification testimony; behavior-only pattern scores reflect that limitation.",
            "Policy routing and graph persistence are not measured by this evaluation.",
            "No benchmark labels, public IEEE-CIS outcomes, or evaluated case narratives are used.",
            "This temporal holdout was rerun after a target-alignment bug was found in the first replay. No features or model hyperparameters were tuned against its outcomes; it is not a fresh blind test.",
        ],
    }
    output.mkdir(parents=True, exist_ok=True)
    for name, payload in (("model.json", artifact), ("report.json", report), ("holdout_predictions.json", predictions)):
        (output / name).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return report
