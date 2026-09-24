from __future__ import annotations

import json
import math
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class GraphToolError(RuntimeError):
    pass


class QueryParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TimedQuery(QueryParameters):
    cutoff: datetime

    @field_validator("cutoff")
    @classmethod
    def unspecified_timezone(cls, value):
        if value.tzinfo is not None:
            raise ValueError("This dataset uses timezone-unspecified timestamps")
        return value


class WindowQuery(TimedQuery):
    start: datetime
    limit: int = Field(default=1000, ge=1, le=10000)

    @model_validator(mode="after")
    def bounded_window(self):
        if self.start.tzinfo is not None or self.start > self.cutoff or (self.cutoff - self.start).total_seconds() > 92 * 86400:
            raise ValueError("Query window must be ordered, timezone-unspecified, and at most 92 days")
        return self


class CardWindow(WindowQuery):
    card_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")


class CustomerWindow(WindowQuery):
    customer_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")


class DeviceWindow(WindowQuery):
    device_id: str = Field(pattern=r"^D-[a-f0-9]{24}$")


class TransactionQuery(TimedQuery):
    transaction_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")


class MemoryQuery(TimedQuery):
    customer_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    exclude_case_id: str = Field(default="", max_length=80)
    limit: int = Field(default=10, ge=1, le=100)


class VectorQuery(TimedQuery):
    query_vector: list[float] = Field(min_length=1536, max_length=1536)
    top_k: int = Field(default=8, ge=1, le=30)
    exclude_case_id: str = Field(default="", max_length=80)

    @field_validator("query_vector")
    @classmethod
    def finite_vector(cls, values):
        if not all(math.isfinite(value) for value in values):
            raise ValueError("Embedding values must be finite")
        return values


class ReadProjection(QueryParameters):
    graph_case_id: str = Field(pattern=r"^CASE-[A-Za-z0-9_-]{1,100}$")


class WriteProjection(ReadProjection):
    case_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    payload_json: str = Field(max_length=1000000)
    revision: int = Field(ge=1)
    source: str = Field(pattern=r"^(agent_assessment|simulation|analyst_validated)$")
    customer_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    card_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    opened_at: datetime
    available_at: datetime
    status: str = Field(pattern=r"^(open|closed_fraud|closed_legitimate|escalated)$")
    verdict: str = Field(pattern=r"^(fraud|legitimate|uncertain)$")
    pattern: str = Field(max_length=80)
    exposure_cents: int = Field(ge=0)


READ_QUERIES: dict[str, type[QueryParameters]] = {
    "transaction_details": TransactionQuery,
    "card_history": CardWindow,
    "customer_history": CustomerWindow,
    "episode_window": CardWindow,
    "baseline_spending": CardWindow,
    "device_neighbors": DeviceWindow,
    "historical_memory": MemoryQuery,
    "vector_context_search": VectorQuery,
}
PROJECTION_QUERIES = {"write_case_projection": WriteProjection, "read_case_projection": ReadProjection}


def decode_result(result: Any) -> list[dict]:
    if result.isError:
        raise GraphToolError("TigerGraph MCP returned a tool error")
    texts = [block.text for block in result.content if block.type == "text"]
    if len(texts) != 1:
        raise GraphToolError("Unexpected TigerGraph MCP response envelope")
    raw_text = texts[0].strip()
    if raw_text.startswith("```"):
        raw_text = raw_text.split("\n", 1)[1].split("\n```", 1)[0]
    try:
        envelope = json.loads(raw_text)
    except (ValueError, TypeError) as exc:
        raise GraphToolError("TigerGraph MCP returned invalid JSON") from exc
    if not isinstance(envelope, dict) or envelope.get("success") is not True:
        raise GraphToolError("TigerGraph query failed; no result is accepted")
    data = envelope.get("data", {})
    rows = data.get("result") if isinstance(data, dict) else None
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise GraphToolError("TigerGraph query result has an unexpected shape")
    return rows


class RestrictedGraphSession:
    """Only this wrapper is exposed to investigation code, never the raw MCP client."""

    def __init__(self, session: ClientSession, graph_name: str, *, cutoff: datetime | None = None, exclude_case_id: str = "", projection: bool = False):
        if not projection and cutoff is None:
            raise ValueError("An investigation graph session requires an availability cutoff")
        self._session = session
        self.graph_name = graph_name
        self.cutoff = cutoff
        self.exclude_case_id = exclude_case_id
        self._queries = PROJECTION_QUERIES if projection else READ_QUERIES
        self.calls = 0

    async def query(self, name: str, parameters: dict) -> list[dict]:
        schema = self._queries.get(name)
        if schema is None:
            raise GraphToolError(f"Installed query is not allowed: {name}")
        parsed = schema.model_validate(parameters)
        if isinstance(parsed, TimedQuery) and self.cutoff is not None and parsed.cutoff > self.cutoff:
            raise GraphToolError("Query exceeds the investigation availability cutoff")
        if self.calls >= 30:
            raise GraphToolError("Graph retrieval budget exhausted")
        arguments = parsed.model_dump()
        if "limit" in arguments:
            arguments["max_rows"] = arguments.pop("limit")
        if isinstance(parsed, (MemoryQuery, VectorQuery)):
            arguments["exclude_case_id"] = self.exclude_case_id
        for key, value in arguments.items():
            if isinstance(value, datetime):
                arguments[key] = value.strftime("%Y-%m-%d %H:%M:%S")
        self.calls += 1
        result = await self._session.call_tool(
            "tigergraph__run_installed_query",
            arguments={"query_name": name, "params": arguments, "graph_name": self.graph_name},
        )
        return decode_result(result)


@asynccontextmanager
async def graph_session(endpoint: str, graph_name: str = "FraudGraph", *, cutoff: datetime | None = None, exclude_case_id: str = "", projection: bool = False):
    # Generic MCP vector search installs/drops temporary queries. It is deliberately
    # absent: vector_context_search is preinstalled and filters before ANN retrieval.
    try:
        async with (
            httpx.AsyncClient(headers={"X-TG-Tools": "run_installed_query"}, timeout=30, follow_redirects=True) as http,
            streamable_http_client(endpoint, http_client=http) as (read, write, _),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            yield RestrictedGraphSession(session, graph_name, cutoff=cutoff, exclude_case_id=exclude_case_id, projection=projection)
    except ExceptionGroup as error:
        # MCP task groups wrap application errors on context exit. Preserve the
        # original conflict/read-back error so the queue can classify the failure.
        cause = error
        while isinstance(cause, ExceptionGroup) and len(cause.exceptions) == 1:
            cause = cause.exceptions[0]
        if cause is error:
            raise
        raise cause from None
