import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from app.graph import TigerGraphMCPStore
from app.tigergraph import GraphToolError


@pytest.fixture(scope="module")
def endpoint():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    process = subprocess.Popen(
        [sys.executable, str(Path(__file__).parent / "support" / "mcp_fixture_server.py"), str(port)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(process.stdout.read().decode())
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=.2):
                    break
            except OSError:
                time.sleep(.05)
        else:
            raise RuntimeError("Local MCP fixture did not become ready")
        yield f"http://127.0.0.1:{port}/mcp/"
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        process.stdout.close()


def payload():
    return {"revision": 1, "source": "agent_assessment", "customer_id": "C1", "card_id": "C1-K1",
            "opened_at": "2016-11-12T12:00:00", "available_at": "2016-11-12T12:00:00",
            "status": "open", "verdict": "uncertain", "pattern": "none", "exposure_usd": 12.34}


def test_projection_over_real_mcp_transport_is_verified_and_idempotent(endpoint):
    store = TigerGraphMCPStore(endpoint)
    expected = payload()
    graph_id = store.write_case("HHG-001", expected)
    assert graph_id == "CASE-HHG-001-R1"
    assert store.read_case(graph_id) == expected
    assert store.tool_calls == 4
    assert store.write_case("HHG-001", expected) == graph_id
    assert store.tool_calls == 5
    with pytest.raises(GraphToolError, match="different content"):
        store.write_case("HHG-001", {**expected, "exposure_usd": 99})


def test_projection_readback_failure_over_mcp_is_not_success(endpoint):
    store = TigerGraphMCPStore(endpoint)
    with pytest.raises(GraphToolError, match="does not match"):
        store.write_case("BAD", payload())
