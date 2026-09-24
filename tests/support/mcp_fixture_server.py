"""Local protocol fixture. It is deliberately not a TigerGraph implementation."""

import sys

from mcp.server.fastmcp import FastMCP

server = FastMCP("HHGOA protocol fixture", host="127.0.0.1", port=int(sys.argv[1]), stateless_http=True, json_response=True)
stored: dict[str, dict] = {}


@server.tool(name="tigergraph__run_installed_query")
def installed_query(query_name: str, params: dict, graph_name: str) -> dict:
    if graph_name != "FraudGraph":
        return {"success": False, "error": "unknown graph"}
    if query_name == "write_case_projection":
        payload = params["payload_json"] if params["case_id"] != "BAD" else '{"corrupted":true}'
        stored[params["graph_case_id"]] = {"v_id": params["graph_case_id"], "attributes": {"payload_json": payload}}
        result = [{"graph_case_id": params["graph_case_id"], "revision": params["revision"]}]
    elif query_name == "read_case_projection":
        found = stored.get(params["graph_case_id"])
        result = [{"cases": [found] if found else []}]
    else:
        return {"success": False, "error": "query not installed in protocol fixture"}
    return {"success": True, "data": {"result": result}}


if __name__ == "__main__":
    server.run(transport="streamable-http")
