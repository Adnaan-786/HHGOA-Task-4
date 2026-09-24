from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path

import httpx

from .calibration import CalibratedScorer, estimate_probability
from .features import FEATURE_VERSION, analyze_behavior
from .graph import GraphStore
from .model import DeterministicModel, ReasoningModel
from .policy import PolicyContext, ResponseKind, next_actions, response_kind, sar_for
from .repository import DatasetRepository
from .schemas import (
    ActionName,
    AnswerFile,
    CaseRecord,
    Evidence,
    EvidenceRequest,
    NextBestActions,
    Pattern,
)


class InvestigationError(ValueError):
    pass


class Investigator:
    def __init__(self, repository: DatasetRepository, graph: GraphStore, calibration_path: Path | None = None, model: ReasoningModel | None = None):
        self.repository = repository
        self.graph = graph
        path = calibration_path or repository.root / "artifacts" / "calibration" / "model.json"
        self.scorer = CalibratedScorer.load(path, repository.root) if path.exists() else None
        self.model = model or DeterministicModel()
        self.last_trace: list[dict] = []

    def run(self, case_id: str, evidence_response: str | None = None, *, simulation: bool = True, revision: int = 1) -> AnswerFile:
        started = time.perf_counter()
        trace: list[dict] = []
        benchmark = self.repository.benchmarks.get(case_id)
        if benchmark is None:
            raise InvestigationError(f"Unknown benchmark case: {case_id}")
        flagged = self.repository.transactions.get(benchmark.flagged_txn_id)
        if flagged is None:
            raise InvestigationError(f"Unknown flagged transaction: {benchmark.flagged_txn_id}")
        if flagged.card_id != benchmark.card_id or flagged.customer_id != benchmark.customer_id:
            raise InvestigationError("Trigger customer/card does not match the flagged transaction")
        response = response_kind(evidence_response)

        def retrieve(name, function):
            if len(trace) >= 30 or time.perf_counter() - started > 300:
                raise InvestigationError("Investigation retrieval budget exhausted; no conclusive verdict was produced")
            call_started = time.perf_counter()
            result = function()
            trace.append({"name": name, "elapsed_s": time.perf_counter() - call_started})
            return result

        analysis = retrieve("local:analyze_behavior", lambda: analyze_behavior(self.repository, flagged.transaction_id, benchmark.opened_at))
        evidence = [Evidence(
            claim=finding.claim, source="external",
            ref=f"local:{FEATURE_VERSION}(transaction={flagged.transaction_id},cutoff={benchmark.opened_at.isoformat()})",
            entity_ids=list(finding.transaction_ids), independent_group=finding.group,
            direction=finding.direction,
        ) for finding in analysis.findings]
        estimate = estimate_probability(analysis.features, benchmark.opened_at, self.scorer)
        if getattr(self.graph, "is_tigergraph", False) and hasattr(self.graph, "investigation_context"):
            graph_context = retrieve("graph:investigation_context", lambda: self.graph.investigation_context(
                transaction_id=flagged.transaction_id, card_id=flagged.card_id,
                customer_id=flagged.customer_id, cutoff=benchmark.opened_at,
            ))
            evidence.append(Evidence(
                claim=(f"TigerGraph returned time-bounded context for the trigger: "
                       f"{graph_context['transaction_count']} transaction rows, "
                       f"{graph_context['card_history_count']} card-history rows, "
                       f"{graph_context['customer_history_count']} customer-history rows, and "
                       f"{graph_context['memory_count']} prior case records."),
                source="graph", ref=f"tigergraph:context(transaction={flagged.transaction_id},cutoff={benchmark.opened_at.isoformat()})",
                entity_ids=[flagged.transaction_id, flagged.card_id, flagged.customer_id],
                independent_group="graph_context", direction="neutral",
            ))
        evidence.append(Evidence(
            claim=(f"Probability {estimate.probability:.4f} comes from a model calibrated on {estimate.population}."
                   if estimate.available else "No historical calibration artifact is available; 0.50 is an explicit uncertainty placeholder, not the bank risk score."),
            source="external", ref=f"calibration:{estimate.model_id}", entity_ids=[flagged.transaction_id],
        ))
        _shared_profiles, candidate_cards = retrieve(
            "local:device_neighbors",
            lambda: self.repository.device_neighbors([flagged.transaction_id], benchmark.opened_at),
        )
        candidate_cards.discard(benchmark.card_id)
        if candidate_cards:
            evidence.append(Evidence(
                claim=f"The profile appears on {len(candidate_cards)} other cards in seven days. These are unconfirmed candidate links; no shared fraud is inferred from the profile alone.",
                source="external", ref=f"local:device_neighbors(transaction={flagged.transaction_id},cutoff={benchmark.opened_at.isoformat()})",
                entity_ids=sorted(candidate_cards),
            ))
        prior = retrieve("local:historical_case_lookup", lambda: self.repository.similar_cases(
            customer_id=benchmark.customer_id, pattern=analysis.pattern, at=benchmark.opened_at, exclude_case_id=case_id,
        ))
        if prior:
            evidence.append(Evidence(
                claim=f"Retrieved {len(prior)} historical cases whose outcomes were available before this investigation, including cleared counterexamples when available.",
                source="external", ref="local:historical_case_lookup", entity_ids=[item.case_id for item in prior],
            ))
        model_context = {
            "case_id": case_id, "trigger_type": benchmark.trigger_type,
            "flagged_transaction": {"id": flagged.transaction_id, "amount_cents": flagged.amount_cents, "channel": flagged.channel},
            "pattern": analysis.pattern, "features": analysis.features,
            "evidence": [{"claim": item.claim, "direction": item.direction, "source": item.source} for item in evidence],
            "missing_evidence": analysis.missing,
        }
        try:
            model_assessment = retrieve("model:assessment", lambda: self.model.assess(model_context))
        except (ValueError, httpx.HTTPError):
            # Free hosted routers can reject strict JSON schemas or rate-limit a
            # request. Keep the investigation truthful and policy-complete by
            # falling back to the deterministic explanation-only model.
            model_assessment = DeterministicModel().assess(model_context)
            trace.append({"name": "model:fallback", "elapsed_s": 0.0})

        reported_denial = benchmark.trigger_type == "customer_report" and any(
            phrase in benchmark.trigger_text.lower() for phrase in ("never", "did not", "not made")
        )
        initial_response = "denied" if reported_denial else None
        initial_probability = max(estimate.probability, .88) if reported_denial else estimate.probability
        support = set(analysis.supporting_groups)
        legitimate = set(analysis.legitimate_groups) - support
        if reported_denial:
            support.add("customer_report")
            evidence.append(Evidence(
                claim="The supplied customer report disputes the flagged transaction.",
                source="customer", ref="case_pack:trigger_text", entity_ids=[benchmark.customer_id],
                independent_group="customer_report", direction="supports",
            ))
        threshold_settled = (
            initial_probability >= .85 and len(support) >= 2
        ) or (initial_probability <= .15 and len(legitimate) >= 2)
        asks_customer = bool(evidence_response) or not (reported_denial or threshold_settled)
        asks_status = analysis.pattern == "card_testing"
        episode = [self.repository.transactions[tid] for tid in analysis.episode_ids]
        exposure = sum(abs(tx.amount_cents) for tx in episode) / 100
        initial_context = PolicyContext(
            probability=initial_probability, pattern=analysis.pattern,
            exposure_usd=0 if initial_probability <= .15 else exposure,
            trigger_type=benchmark.trigger_type, customer_response=initial_response,
            evidence_requested=asks_customer or asks_status, shared_origin=bool(candidate_cards),
            independent_supporting_groups=len(support), independent_legitimate_groups=len(legitimate),
            verdict=self._verdict(initial_probability),
        )
        probability = initial_probability
        conflict = reported_denial and response == ResponseKind.confirmed
        if conflict:
            probability = .5
        elif response == ResponseKind.confirmed:
            probability = .05
        elif response == ResponseKind.denied:
            probability = max(initial_probability, .88)
        simulated_wait = simulation and response is None
        final_context = replace(
            initial_context, probability=probability,
            exposure_usd=0 if probability <= .15 else exposure,
            customer_response=(response.value if response else initial_response or ("no_response" if simulated_wait else None)),
            hours_since_request=24 if asks_customer and simulated_wait and not reported_denial else 0,
            evidence_conflict=conflict, verdict=self._verdict(probability),
            independent_supporting_groups=len(support | ({"customer_validation"} if response == ResponseKind.denied else set())),
        )
        initial, final, changed = next_actions(initial_context, final_context)
        requests: list[EvidenceRequest] = []
        if asks_customer:
            requests.append(EvidenceRequest(
                type="customer_validation", asked_after_step=len(trace),
                assumed_response=(f"Simulated response: {response.value}; this is assumed testimony for the demo, not an observed customer reply."
                                  if response else "Simulated no response within 24 hours. The request would distinguish an authorized purchase from unauthorized use."
                                  if simulated_wait else "Pending authenticated customer response; no testimony has been assumed."),
                simulated=bool(response or simulated_wait),
            ))
        if asks_status or (asks_customer and response is None):
            requests.append(EvidenceRequest(
                type="analyst_info", asked_after_step=len(trace),
                assumed_response="Assumed unavailable: authorization/settlement status. This request resolves the status-dependent branches of R4/R5; no pending or cleared status is invented.",
                simulated=bool(response or simulated_wait),
            ))
        if response:
            evidence.append(Evidence(
                claim=f"Simulated evidence response: {response.value}.",
                source="customer" if response in {ResponseKind.confirmed, ResponseKind.denied} else "external",
                ref="evidence_requests:0", entity_ids=[benchmark.customer_id],
                independent_group="customer_validation", simulated=True,
                direction="supports" if response == ResponseKind.denied else "contradicts" if response == ResponseKind.confirmed else "neutral",
            ))
        verdict = self._verdict(probability)
        affected_ids = [] if verdict == "legitimate" else list(analysis.episode_ids)
        final_exposure = 0 if verdict == "legitimate" else exposure
        pattern = Pattern.none if verdict == "legitimate" else Pattern(analysis.pattern)
        verification_settled = not conflict and (reported_denial or response in {ResponseKind.confirmed, ResponseKind.denied})
        if conflict or any(item.action == ActionName.ESCALATE_TO_ANALYST for item in final):
            status = "escalated"
        elif verification_settled or threshold_settled:
            status = "closed_fraud" if verdict == "fraud" else "closed_legitimate" if verdict == "legitimate" else "open"
        else:
            status = "open"

        if conflict:
            stop_reason = "Conflicting customer evidence requires analyst review under R8."
        elif verification_settled:
            stop_reason = ("The explicitly simulated verification response settles the flagged transaction in this demo scenario."
                           if response else "The customer denial in the supplied trigger settles the flagged transaction under R2.")
        elif threshold_settled:
            stop_reason = "The policy §6 probability threshold is supported by at least two independent evidence groups in the same direction."
        else:
            stop_reason = ("Further retrieval from the supplied data cannot resolve the missing verification or status information; the case remains open or escalated while the evidence request is pending."
                           if not simulated_wait else
                           "Further retrieval from the supplied data cannot resolve the missing verification or status information; the case remains open or escalated after the explicitly simulated timeout.")
        scope = ("Historical calibration applies to selected investigations, not all bank transactions."
                 if estimate.available else "No calibration model is available; the probability is an uncertainty placeholder.")
        summary = (
            f"The {benchmark.trigger_type} trigger for {flagged.transaction_id} was investigated using a pre-episode baseline and time-bounded activity. "
            f"The provisional assessment is {verdict} with probability {probability:.4f} and {len(affected_ids)} transactions in the candidate episode. "
            f"{scope} "
            f"Missing information includes {', '.join(sorted(set(analysis.missing) | set(model_assessment.missing_evidence)))}. "
            f"Analyst explanation: {model_assessment.explanation}"
        )
        sar = sar_for(
            final_context, customer_id=benchmark.customer_id, card_id=benchmark.card_id,
            dates=[episode[0].timestamp.date().isoformat(), episode[-1].timestamp.date().isoformat()],
            subjects=[benchmark.customer_id, benchmark.card_id],
            pattern_description="; ".join(f.claim for f in analysis.findings if f.direction == "supports") or analysis.pattern,
            amount=final_exposure,
        )
        record = CaseRecord(
            status=status, verdict=verdict, fraud_probability=round(probability, 6), pattern=pattern,
            affected_txn_ids=affected_ids, first_suspicious_txn_id=affected_ids[0] if affected_ids else "",
            exposure_usd=final_exposure, evidence=evidence, similar_prior_cases=[item.case_id for item in prior],
            summary=summary,
        )
        projection = {
            "status": status, "verdict": verdict, "pattern": pattern.value,
            "probability": record.fraud_probability, "exposure_usd": final_exposure,
            "affected_txn_ids": affected_ids, "evidence": [item.model_dump(mode="json") for item in evidence],
            "assessment_model_id": estimate.model_id, "feature_version": FEATURE_VERSION,
            "source": "agent_assessment", "simulated": any(item.simulated for item in requests),
            "revision": revision,
            "customer_id": benchmark.customer_id, "card_id": benchmark.card_id,
            "opened_at": benchmark.opened_at.isoformat(), "available_at": benchmark.opened_at.isoformat(),
        }
        graph_calls_before = getattr(self.graph, "tool_calls", 0)
        graph_case_id = self.graph.write_case(case_id, projection)
        read_back = retrieve("case_projection_readback", lambda: self.graph.read_case(graph_case_id))
        record.written_to_graph = bool(self.graph.is_tigergraph and read_back and all(read_back.get(k) == v for k, v in projection.items()))
        record.graph_case_id = graph_case_id if record.written_to_graph else ""
        self.last_trace = trace
        return AnswerFile(
            case_id=case_id, case=record, evidence_requests=requests,
            next_best_actions=NextBestActions(initial=initial, final=final, what_changed=changed),
            sar=sar, stop_reason=stop_reason,
            tool_calls=(sum(item["name"].startswith(("local:", "case_")) for item in trace) + self.graph.tool_calls - graph_calls_before
                        if hasattr(self.graph, "tool_calls") else sum(item["name"].startswith(("local:", "case_")) for item in trace)),
            tokens=model_assessment.tokens,
            latency_s=round(time.perf_counter() - started, 6),
        )

    @staticmethod
    def _verdict(probability: float) -> str:
        if probability >= .70:
            return "fraud"
        if probability <= .15:
            return "legitimate"
        return "uncertain"
