from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .features import FEATURE_VERSION
from .ingest import sha256


def sigmoid(value: float) -> float:
    if value >= 0:
        return 1 / (1 + math.exp(-value))
    exp_value = math.exp(value)
    return exp_value / (1 + exp_value)


@dataclass(frozen=True)
class ProbabilityEstimate:
    probability: float
    model_id: str
    population: str
    available: bool


class CalibratedScorer:
    """Portable numeric model; loading does not execute a pickle or arbitrary code."""

    def __init__(self, artifact: dict):
        if artifact.get("feature_version") != FEATURE_VERSION:
            raise ValueError("Calibration feature version mismatch")
        self.artifact = artifact
        self.names = tuple(artifact["feature_names"])
        self.mean = tuple(artifact["mean"])
        self.scale = tuple(artifact["scale"])
        self.coefficients = tuple(artifact["coefficients"])
        if not self.names or len(set(self.names)) != len(self.names):
            raise ValueError("Calibration feature names must be nonempty and unique")
        if not all(len(v) == len(self.names) for v in (self.mean, self.scale, self.coefficients)):
            raise ValueError("Calibration dimensions differ")
        self.intercept = float(artifact["intercept"])
        self.slope = float(artifact["calibration_slope"])
        self.offset = float(artifact["calibration_intercept"])
        values = (*self.mean, *self.scale, *self.coefficients, self.intercept, self.slope, self.offset)
        if not all(math.isfinite(v) for v in values) or not all(v > 0 for v in self.scale):
            raise ValueError("Calibration contains invalid numeric values")
        self.available_at = datetime.fromisoformat(artifact["available_at"])
        self.model_id = hashlib.sha256(json.dumps(artifact, sort_keys=True).encode()).hexdigest()

    @classmethod
    def load(cls, path: Path, source_root: Path | None = None) -> CalibratedScorer:
        artifact = json.loads(path.read_text())
        if artifact.get("feature_code_sha256") != sha256(Path(__file__).with_name("features.py")):
            raise ValueError("Calibration feature code has changed; rebuild the artifact explicitly")
        if source_root is not None:
            for name in ("transactions.csv", "identity.csv", "closed_cases_history.csv"):
                if artifact.get("source_sha256", {}).get(name) != sha256(source_root / name):
                    raise ValueError(f"Calibration source version differs: {name}")
        return cls(artifact)

    def estimate(self, features: dict[str, float], cutoff: datetime) -> ProbabilityEstimate:
        if cutoff < self.available_at:
            raise ValueError("Calibration uses outcomes unavailable at this investigation cutoff")
        if set(features) != set(self.names) or not all(math.isfinite(v) for v in features.values()):
            raise ValueError("Features do not match the calibrated model")
        linear = self.intercept + sum(
            coefficient * (features[name] - mean) / scale
            for name, mean, scale, coefficient in zip(self.names, self.mean, self.scale, self.coefficients, strict=True)
        )
        return ProbabilityEstimate(sigmoid(self.slope * linear + self.offset), self.model_id,
                                   "selected historical investigations, not all bank transactions", True)


def estimate_probability(features: dict[str, float], cutoff: datetime, scorer: CalibratedScorer | None) -> ProbabilityEstimate:
    if scorer is None:
        return ProbabilityEstimate(0.5, "unavailable", "uncalibrated; insufficient basis for a probability estimate", False)
    return scorer.estimate(features, cutoff)
