import asyncio
import json
from datetime import datetime
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.graph import TigerGraphMCPStore
from app.tigergraph import GraphToolError, RestrictedGraphSession, decode_result


class SessionDouble:
    def __init__(self):
        self.calls = []

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return SimpleNamespace(isError=False, content=[SimpleNamespace(type="text", text=json.dumps({"success": True, "data": {"result": [{"transactions": []}]}}))])


def boundary(projection=False):
    session = SessionDouble()
    return session, RestrictedGraphSession(session, "FraudGraph", cutoff=datetime.fromisoformat("2016-11-12"), projection=projection)


@pytest.mark.parametrize("name", ["gsql", "run_query", "drop_graph", "search_top_k_similarity", "write_case_projection", "read_case_projection"])
def test_agent_cannot_call_arbitrary_or_mutating_tools(name):
    session, client = boundary()
    with pytest.raises(GraphToolError, match="not allowed"):
        asyncio.run(client.query(name, {}))
    assert session.calls == []


def test_cutoff_cannot_be_advanced_by_tool_arguments():
    session, client = boundary()
    with pytest.raises(GraphToolError, match="availability cutoff"):
        asyncio.run(client.query("transaction_details", {"transaction_id": "1", "cutoff": "2016-11-13"}))
    assert session.calls == []


def test_only_installed_query_tool_reaches_mcp():
    session, client = boundary()
    assert asyncio.run(client.query("transaction_details", {"transaction_id": "1", "cutoff": "2016-11-12"})) == [{"transactions": []}]
    name, arguments = session.calls[0]
    assert name == "tigergraph__run_installed_query"
    assert arguments == {"query_name": "transaction_details", "params": {"transaction_id": "1", "cutoff": "2016-11-12 00:00:00"}, "graph_name": "FraudGraph"}


def test_query_text_injection_is_rejected_before_call():
    session, client = boundary()
    with pytest.raises(ValidationError):
        asyncio.run(client.query("transaction_details", {"transaction_id": "1; DROP ALL", "cutoff": "2016-11-12"}))
    assert session.calls == []


@pytest.mark.parametrize("vector", [[0.] * 100, [float("nan")] * 1536])
def test_invalid_vectors_never_reach_server(vector):
    session, client = boundary()
    with pytest.raises(ValidationError):
        asyncio.run(client.query("vector_context_search", {"query_vector": vector, "cutoff": "2016-11-12"}))
    assert session.calls == []


def test_projection_session_has_separate_allowlist():
    session, client = boundary(projection=True)
    with pytest.raises(GraphToolError, match="not allowed"):
        asyncio.run(client.query("card_history", {}))
    assert session.calls == []


def test_server_error_envelope_cannot_become_evidence():
    result = SimpleNamespace(isError=False, content=[SimpleNamespace(type="text", text='{"success":false,"error":"unavailable"}')])
    with pytest.raises(GraphToolError, match="query failed"):
        decode_result(result)


def test_readback_must_match_requested_vertex():
    with pytest.raises(GraphToolError, match="unexpected case"):
        TigerGraphMCPStore._payload([{"cases": [{"v_id": "OTHER", "attributes": {"payload_json": "{}"}}]}], "CASE-HHG-001-R1")


def test_retrieval_call_limit():
    session, client = boundary()
    client.calls = 30
    with pytest.raises(GraphToolError, match="budget"):
        asyncio.run(client.query("transaction_details", {"transaction_id": "1", "cutoff": "2016-11-12"}))
    assert session.calls == []


def test_evaluated_case_exclusion_is_owned_by_the_application():
    session = SessionDouble()
    client = RestrictedGraphSession(session, "FraudGraph", cutoff=datetime.fromisoformat("2016-11-12"), exclude_case_id="CC-1")
    asyncio.run(client.query("historical_memory", {"customer_id": "C1", "cutoff": "2016-11-12", "exclude_case_id": ""}))
    assert session.calls[0][1]["params"]["exclude_case_id"] == "CC-1"
