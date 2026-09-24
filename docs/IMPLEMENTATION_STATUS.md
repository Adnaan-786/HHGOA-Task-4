# Implementation status

This is a verified read-only pilot implementation of the approved plan. The unchanged organizer requirements remain in `README.md`. No judging answer key is present, and no hidden accuracy score is claimed.

## Completed corrections

- Restored the organizer README byte for byte. SHA-256: `57e6dd7c7766b4efe3e903f111be2f4237d058d53e3b1738d6e56dcd6cecda59`.
- Separated initial and final policy contexts. Structured simulated responses no longer rewrite initial recommendations or interpret arbitrary text as confirmation.
- Added deterministic case-opening, evidence-count, reporting, timeout, approval-route, recurring-dispute, conflict, and all-card-block checks. Strong suspicion at 0.70 with two supporting evidence groups remains a pilot assumption.
- Made the testing-sequence window one hour. Corrected the region baseline to exclude the candidate episode and bounded profile candidates to seven days.
- Preserved missing device-profile components and stopped treating generic profile matches as confirmed shared fraud. Candidate links remain evidence, not confirmed connected cards.
- Included cleared historical counterexamples and an explicit own-case exclusion option. This is local lookup, not vector retrieval.
- Local persistence now exports `written_to_graph=false` and an empty graph ID. The application only reports verified graph persistence after a TigerGraph-designated adapter returns matching projection data.
- Added exact-cent exposure comparisons, unique/chronological transaction validation, and submission serialization that excludes internal evidence metadata.
- Preserved the previous 20 outputs under `artifacts/legacy-answers-20260921/`; they are not valid current submission results.

## Required work still outstanding

1. **Data foundation:** local normalization, the frozen card mapping, source-preserving archives, manifest checks, and all 14,975 supplied card references have been verified. The package contains 590,742 transactions, 144,432 identities, and 5,565 historical cases. The normalized data is now loaded into the configured Savanna workspace with source counts reconciled after header-artifact cleanup.
2. **TigerGraph:** the isolated `HHGOAFraudGraph` schema, loaders, ten compiled parameterized queries, restricted MCP client, and versioned case projection/read-back are live. Verified counts are 590,742 transactions, 14,317 cards, 13,553 customers, 5,565 historical cases, and matching relationship counts. Document embeddings are not loaded yet, so vector retrieval remains a documented next step.
3. **Investigation accuracy:** a chronological historical training/calibration/holdout pipeline now replaces the bank-risk-score probability heuristic. Its corrected historical holdout Brier score is 0.03188, but pattern accuracy is only 19.24% and mean episode Jaccard is 0.6361. These are results on selected historical investigations, not hidden benchmark accuracy or bank-wide performance. Improve pattern identification, episode selection, legitimate explanations, and corroborated shared-origin analysis. Do not tune verdicts to force the benchmark's stated class balance.
4. **Durable workflow:** the API now supports a pending evidence phase, immutable revision snapshots, idempotency keys, PostgreSQL persistence, and restart recovery for case/approval records. OpenRouter structured assessment is wired; when the free router rejects strict JSON, the investigation records a deterministic explanation fallback while policy and actions remain application-controlled. LangGraph checkpoints, job leases, and outbox retries remain outstanding.
5. **Analyst workflow:** the frontend uses the live case queue, shows bounded evidence and uncertainty, offers controlled response simulations, displays revision history, and records L1/L2 approvals as simulated-only. A live TigerGraph evidence context and case projection are now visible to the application. Authentication and production identity-backed roles remain outstanding.
6. **Benchmark evaluation:** all 20 cases were run through the live TigerGraph-backed service and exported to `cases/`. All 20 files are schema-valid and graph-persisted; the current provisional distribution is 17 fraud, 2 uncertain, and 1 legitimate. Hidden answer-key accuracy remains unknown.
7. **Pilot operations:** PostgreSQL migrations, Cognito roles, ECS/RDS/S3/Secrets/CloudWatch Terraform, recovery, backup/restore, audit retention, and access-control tests. No cloud deployment has been performed.
8. **Submission:** setup/architecture documentation, verified 20 answers, recorded 3–5 minute demo, blog draft, and social draft. Publishing remains separate from local implementation.

## Remaining policy integration gaps

The policy module can evaluate facts such as verified recurrence, payment status, compromised credentials, and corroborated shared-origin fraud. The investigator does not yet collect all of those facts. Payment-status evidence requests are recorded, but authenticated responses and a durable 24-hour timer are not implemented; unattended local runs explicitly simulate the timeout. Stopping and exposure inference need further evaluation. Passing policy tests does not establish end-to-end compliance.

## Local verification

Run `HHGOA_GRAPH_BACKEND=local .venv/bin/pytest -q` for the local suite; the current result is 123 passed. The live integration was verified separately with Savanna counts, an installed time-bounded traversal, a case projection read-back, and the 20-case export. The browser/API remains read-only and action execution is simulated.

Account setup and live integration commands are in `docs/SETUP.md`. Remaining submission work is the demo/package polish, document embeddings, and production authentication/operations; these are separate from the verified benchmark export.
