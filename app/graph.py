from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any


class GraphStore:
    is_tigergraph = False

    def write_case(self, case_id: str, payload: dict[str, Any]) -> str:
        raise NotImplementedError

    def read_case(self, graph_case_id: str) -> dict[str, Any] | None:
        raise NotImplementedError


class LocalGraphStore(GraphStore):
    """Development graph adapter.

    The production adapter is intentionally separate so local benchmark runs do
    not pretend that a JSONL file is TigerGraph. The same case projection payload
    is sent to TigerGraph in production.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._cases: dict[str, dict[str, Any]] = {}
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    item = json.loads(line)
                    self._cases[item["graph_case_id"]] = item

    def write_case(self, case_id: str, payload: dict[str, Any]) -> str:
        graph_case_id = f"CASE-{case_id}"
        item = {"graph_case_id": graph_case_id, "case_id": case_id, **payload}
        self._cases[graph_case_id] = item
        with self.path.open("w", encoding="utf-8") as fh:
            for value in self._cases.values():
                fh.write(json.dumps(value, sort_keys=True) + "\n")
        return graph_case_id

    def read_case(self, graph_case_id: str) -> dict[str, Any] | None:
        return self._cases.get(graph_case_id)


class TigerGraphMCPStore(GraphStore):
    """Versioned projection through installed MCP queries with exact read-back.

    This client is implemented; deployed query compatibility requires a live
    Savanna integration test. It does not make local evidence into graph evidence.
    """

    is_tigergraph = True

    def __init__(self, endpoint: str, graph_name: str = "FraudGraph"):
        self.endpoint = endpoint
        self.graph_name = graph_name
        self.tool_calls = 0

    def write_case(self, case_id: str, payload: dict[str, Any]) -> str:
        from .tigergraph import GraphToolError, graph_session

        revision = payload.get("revision", 1)
        graph_case_id = f"CASE-{case_id}-R{revision}"
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))

        async def write():
            async with graph_session(self.endpoint, self.graph_name, projection=True) as session:
                try:
                    existing = self._payload(await session.query("read_case_projection", {"graph_case_id": graph_case_id}), graph_case_id)
                    if existing is not None:
                        if existing != payload:
                            raise GraphToolError("Case revision already exists with different content; allocate a new revision")
                        return graph_case_id
                    await session.query("write_case_projection", {
                        "graph_case_id": graph_case_id, "case_id": case_id, "payload_json": serialized,
                        "revision": revision, "source": payload.get("source", "agent_assessment"),
                        "customer_id": payload["customer_id"], "card_id": payload["card_id"],
                        "opened_at": payload["opened_at"], "available_at": payload["available_at"],
                        "status": payload["status"], "verdict": payload["verdict"], "pattern": payload["pattern"],
                        "exposure_cents": int(Decimal(str(payload["exposure_usd"])) * 100),
                    })
                    verified = self._payload(await session.query("read_case_projection", {"graph_case_id": graph_case_id}), graph_case_id)
                    if verified != payload:
                        raise GraphToolError("TigerGraph projection read-back does not match the requested revision")
                    return graph_case_id
                finally:
                    self.tool_calls += session.calls
        return asyncio.run(write())

    def read_case(self, graph_case_id: str) -> dict[str, Any] | None:
        from .tigergraph import graph_session

        async def read():
            async with graph_session(self.endpoint, self.graph_name, projection=True) as session:
                try:
                    result = await session.query("read_case_projection", {"graph_case_id": graph_case_id})
                    return self._payload(result, graph_case_id)
                finally:
                    self.tool_calls += session.calls
        return asyncio.run(read())

    def investigation_context(self, *, transaction_id: str, card_id: str, customer_id: str, cutoff: datetime) -> dict[str, int]:
        """Run the installed, cutoff-bound read queries used by an investigation."""
        from .tigergraph import graph_session

        async def read():
            async with graph_session(self.endpoint, self.graph_name, cutoff=cutoff, exclude_case_id="") as session:
                try:
                    transaction = await session.query("transaction_details", {"transaction_id": transaction_id, "cutoff": cutoff})
                    card = await session.query("card_history", {"card_id": card_id, "start": cutoff - timedelta(days=90), "cutoff": cutoff, "limit": 1000})
                    customer = await session.query("customer_history", {"customer_id": customer_id, "start": cutoff - timedelta(days=90), "cutoff": cutoff, "limit": 1000})
                    memory = await session.query("historical_memory", {"customer_id": customer_id, "cutoff": cutoff, "exclude_case_id": "", "limit": 10})
                    return {
                        "transaction_count": len(transaction), "card_history_count": len(card),
                        "customer_history_count": len(customer), "memory_count": len(memory),
                    }
                finally:
                    self.tool_calls += session.calls
        return asyncio.run(read())

    @staticmethod
    def _payload(rows: list[dict], graph_case_id: str) -> dict | None:
        from .tigergraph import GraphToolError

        vertices = [vertex for row in rows for vertex in row.get("cases", [])]
        if not vertices:
            return None
        if len(vertices) != 1 or vertices[0].get("v_id") != graph_case_id:
            raise GraphToolError("Projection query returned an unexpected case")
        try:
            payload = json.loads(vertices[0]["attributes"]["payload_json"])
        except (KeyError, ValueError, TypeError) as exc:
            raise GraphToolError("Projection payload is missing or malformed") from exc
        if not isinstance(payload, dict):
            raise GraphToolError("Projection payload must be an object")
        return payload
