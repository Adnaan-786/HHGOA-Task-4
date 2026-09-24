import unittest
from pathlib import Path

from app.answer import validate_answer
from app.repository import DatasetRepository
from app.schemas import AnswerFile, CaseRecord, NextBestActions, Pattern, SAR


class AnswerValidationTests(unittest.TestCase):
    def test_benchmark_answer_is_valid(self):
        root = Path(__file__).resolve().parents[1]
        repo = DatasetRepository(root)
        from app.graph import LocalGraphStore
        from app.investigator import Investigator
        answer = Investigator(repo, LocalGraphStore(root / "runtime" / "test-cases.jsonl")).run("HHG-001")
        validate_answer(answer, repo)

    def test_legitimate_answer_cannot_claim_exposure(self):
        case = CaseRecord(status="closed_legitimate", verdict="legitimate", fraud_probability=0.1, pattern=Pattern.none, exposure_usd=0, summary="ok")
        answer = AnswerFile(case_id="HHG-001", case=case, next_best_actions=NextBestActions(), sar=SAR(file=False, reason="none"), stop_reason="done", tool_calls=0, tokens=0, latency_s=0)
        repo = DatasetRepository(Path(__file__).resolve().parents[1])
        validate_answer(answer, repo)
