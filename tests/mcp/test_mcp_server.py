from __future__ import annotations

from pathlib import Path

from sliderefine.mcp.server import _handle


def test_mcp_tools_list():
    response = _handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert response is not None
    names = {tool["name"] for tool in response["result"]["tools"]}
    assert {"convert_image", "inspect_document", "query_nodes", "apply_operations", "render_preview", "export_document"} <= names


def test_mcp_rejects_paths_outside_sandbox():
    outside = (Path.home() / "sliderefine-outside.srf").resolve().as_uri()
    response = _handle(
        {
            "jsonrpc": "2.0",
            "id": 8,
            "method": "tools/call",
            "params": {"name": "inspect_document", "arguments": {"documentUri": outside}},
        }
    )
    assert response and "error" in response
    assert "outside MCP sandbox" in response["error"]["message"]


def test_mcp_feedback_loop(fixture_image, tmp_path):
    document_path = tmp_path / "mcp.srf"
    converted = _handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "convert_image",
                "arguments": {
                    "inputUri": Path(fixture_image).resolve().as_uri(),
                    "outputUri": document_path.resolve().as_uri(),
                    "options": {"skipTextDetection": True},
                },
            },
        }
    )
    assert converted and "result" in converted
    document_uri = converted["result"]["content"][0]["json"]["documentUri"]

    queried = _handle(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "query_nodes",
                "arguments": {"documentUri": document_uri, "types": ["image"], "limit": 1},
            },
        }
    )
    node_id = queried["result"]["content"][0]["json"]["nodes"][0]["id"]

    dry_run = _handle(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "apply_operations",
                "arguments": {
                    "documentUri": document_uri,
                    "operationId": "op-mcp-test-dry",
                    "expectedRevision": 1,
                    "dryRun": True,
                    "operations": [{"type": "translate", "nodeIds": [node_id], "dx": 1, "dy": 1}],
                },
            },
        }
    )
    assert dry_run["result"]["content"][0]["json"]["revision"] == 1

    applied = _handle(
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {
                "name": "apply_operations",
                "arguments": {
                    "documentUri": document_uri,
                    "operationId": "op-mcp-test",
                    "expectedRevision": 1,
                    "operations": [{"type": "translate", "nodeIds": [node_id], "dx": 1, "dy": 1}],
                },
            },
        }
    )
    assert applied["result"]["content"][0]["json"]["revision"] == 2

    preview = _handle(
        {
            "jsonrpc": "2.0",
            "id": 6,
            "method": "tools/call",
            "params": {"name": "render_preview", "arguments": {"documentUri": document_uri}},
        }
    )
    assert preview["result"]["content"][0]["json"]["preview"]["mimeType"] == "image/png"

    exported = tmp_path / "mcp.svg"
    export = _handle(
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {
                "name": "export_document",
                "arguments": {"documentUri": document_uri, "format": "svg", "outputUri": exported.resolve().as_uri()},
            },
        }
    )
    assert export["result"]["content"][0]["json"]["artifact"]["size"] > 0


def test_mcp_convert_timeout_returns_typed_error(fixture_image, tmp_path):
    response = _handle(
        {
            "jsonrpc": "2.0",
            "id": 9,
            "method": "tools/call",
            "params": {
                "name": "convert_image",
                "arguments": {
                    "inputUri": Path(fixture_image).resolve().as_uri(),
                    "outputUri": (tmp_path / "timeout.srf").resolve().as_uri(),
                    "options": {"skipTextDetection": True, "timeoutMs": 0},
                },
            },
        }
    )
    assert response and "error" in response
    assert response["error"]["data"]["code"] == "CONVERSION_TIMEOUT"
