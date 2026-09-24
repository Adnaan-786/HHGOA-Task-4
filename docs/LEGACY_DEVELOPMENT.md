# Archived development notes

These notes describe the earlier scaffold and contain claims that have not been verified. The root README is the unchanged organizer specification.

# HHGOA Fraud Investigation Agent

This repository contains the first runnable implementation of the TigerGraph
agentic fraud investigation pilot described in the challenge brief.

The local mode reads the supplied CSV benchmark, derives the dataset-specific
card IDs, runs graph-shaped evidence queries over the read-only repository,
applies the Fraud Policy deterministically, writes a case projection to JSONL,
and returns the required answer-file schema. The production boundary is the
TigerGraph MCP adapter and the PostgreSQL schema in `db/schema.sql`.

## Run locally

```bash
pip install -e '.[dev]'
python -m unittest discover -s tests -v
python -m app.cli investigate HHG-001 --data-dir /Users/adnaan/Downloads/HHGOA
python -m app.cli benchmark --data-dir /Users/adnaan/Downloads/HHGOA --output cases
python -m app.cli validate --data-dir /Users/adnaan/Downloads/HHGOA
uvicorn app.main:app --reload
```

The API exposes `GET /health`, `POST /api/v1/investigations`, and
`GET /api/v1/investigations/{case_id}`. The frontend in `frontend/` is a React
analyst workspace that calls the API and displays evidence, case memory, SAR
status, and initial/final recommendations.

Run the UI with `cd frontend && npm install && npm run dev`. Vite proxies API
requests to the local FastAPI server.

## Production integration boundary

Deploy `graph/schema.gsql` and the approved parameterized queries listed in
`graph/queries.gsql` to TigerGraph 4.2+/Savanna. Then implement the methods in
`TigerGraphMCPStore` using the restricted MCP session. The current local adapter
is deliberately explicit so a local benchmark run cannot be mistaken for a
TigerGraph write.

The repository never treats a risk score as a verdict, never executes financial
actions, and marks benchmark evidence requests as simulated or unavailable.
