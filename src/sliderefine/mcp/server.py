from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import url2pathname

from sliderefine.application.conversion_service import ConversionOptions, ConversionTimeout, convert
from sliderefine.application.operation_service import OperationError, apply_transaction
from sliderefine.cli.main import _inspect
from sliderefine.exporters.png import render_png
from sliderefine.exporters.srf import load_document, save_document
from sliderefine.exporters.svg import export_svg


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _sandbox_roots() -> list[Path]:
    configured = os.environ.get("SLIDEREFINE_MCP_ROOTS")
    roots = configured.split(os.pathsep) if configured else [os.getcwd(), tempfile.gettempdir()]
    return [Path(root).expanduser().resolve() for root in roots if root]


TOOLS: list[dict[str, Any]] = [
    {"name": "convert_image", "description": "Convert an image/PPTX to a .srf document."},
    {"name": "inspect_document", "description": "Inspect document summary, warnings, and text."},
    {"name": "query_nodes", "description": "Query nodes by type and confidence."},
    {"name": "apply_operations", "description": "Apply a revision-checked operation transaction."},
    {"name": "render_preview", "description": "Render a preview PNG and return its URI."},
    {"name": "export_document", "description": "Export the document to SVG or PNG."},
]


def _path_from_uri(uri: str) -> Path:
    parsed = urlparse(uri)
    if parsed.scheme == "file":
        path = Path(url2pathname(parsed.path)).resolve()
    else:
        path = Path(uri).expanduser().resolve()
    if not any(_is_relative_to(path, root) for root in _sandbox_roots()):
        raise ValueError(f"Path outside MCP sandbox: {path}")
    return path


def _file_uri(path: Path) -> str:
    return path.resolve().as_uri()


def _tool_convert(args: dict[str, Any]) -> dict[str, Any]:
    input_path = _path_from_uri(args["inputUri"])
    output_format = args.get("outputFormat", "srf")
    output_uri = args.get("outputUri")
    output_path = _path_from_uri(output_uri) if output_uri else Path(tempfile.gettempdir()) / f"{input_path.stem}.srf"
    options = args.get("options", {})
    result = convert(
        input_path,
        ConversionOptions(
            ocr_engine=options.get("ocrEngine", "none"),
            colors=int(options.get("colors", 12)),
            skip_text_detection=bool(options.get("skipTextDetection", True)),
            timeout_ms=int(options["timeoutMs"]) if "timeoutMs" in options else None,
        ),
    )
    if output_format != "srf":
        raise ValueError("MCP convert_image currently returns srf output")
    save_document(result.document, output_path)
    return {
        "documentUri": _file_uri(output_path),
        "revision": result.document["revision"],
        "slides": len(result.document["slides"]),
        "warnings": result.warnings,
    }


def _tool_inspect(args: dict[str, Any]) -> dict[str, Any]:
    document = load_document(_path_from_uri(args["documentUri"]))
    return _inspect(document)


def _tool_query(args: dict[str, Any]) -> dict[str, Any]:
    document = load_document(_path_from_uri(args["documentUri"]))
    types = set(args.get("types") or [])
    limit = int(args.get("limit") or 100)
    confidence_below = args.get("confidenceBelow")
    nodes = []
    for node in document["nodes"].values():
        if types and node["type"] not in types:
            continue
        confidence = float(node.get("provenance", {}).get("confidence") or 0)
        if confidence_below is not None and confidence >= float(confidence_below):
            continue
        nodes.append(node)
    return {"revision": document["revision"], "nodes": nodes[:limit], "count": min(len(nodes), limit)}


def _tool_apply(args: dict[str, Any]) -> dict[str, Any]:
    path = _path_from_uri(args["documentUri"])
    document = load_document(path)
    transaction = {
        "schemaVersion": "1.0.0",
        "operationId": args.get("operationId", "op-mcp"),
        "expectedRevision": args["expectedRevision"],
        "dryRun": bool(args.get("dryRun", False)),
        "operations": args.get("operations", []),
    }
    updated, result = apply_transaction(document, transaction)
    if not transaction["dryRun"]:
        save_document(updated, path)
    return result


def _tool_render(args: dict[str, Any]) -> dict[str, Any]:
    document_path = _path_from_uri(args["documentUri"])
    document = load_document(document_path)
    output_path = Path(tempfile.gettempdir()) / f"{document['documentId']}-{args.get('slideId', 'slide')}.png"
    render_png(document, output_path, slide_id=args.get("slideId"), archive_path=document_path, scale=float(args.get("scale", 1)))
    return {"preview": {"uri": _file_uri(output_path), "mimeType": "image/png", "size": output_path.stat().st_size}}


def _tool_export(args: dict[str, Any]) -> dict[str, Any]:
    document_path = _path_from_uri(args["documentUri"])
    output_path = _path_from_uri(args["outputUri"])
    document = load_document(document_path)
    if args.get("format", "svg") == "svg":
        export_svg(document, output_path, archive_path=document_path)
    else:
        render_png(document, output_path, archive_path=document_path)
    return {"artifact": {"uri": _file_uri(output_path), "size": output_path.stat().st_size}}


CALLS = {
    "convert_image": _tool_convert,
    "inspect_document": _tool_inspect,
    "query_nodes": _tool_query,
    "apply_operations": _tool_apply,
    "render_preview": _tool_render,
    "export_document": _tool_export,
}


def _handle(message: dict[str, Any]) -> dict[str, Any] | None:
    method = message.get("method")
    msg_id = message.get("id")
    try:
        result: dict[str, Any]
        if method == "initialize":
            result = {"protocolVersion": "2025-11-25", "serverInfo": {"name": "sliderefine", "version": "0.1.0"}}
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            params = message.get("params", {})
            result = {"content": [{"type": "json", "json": CALLS[params["name"]](params.get("arguments", {}))}]}
        elif method == "notifications/initialized":
            return None
        else:
            raise ValueError(f"Unsupported method: {method}")
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}
    except OperationError as exc:
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32000, "message": exc.message, "data": {"code": exc.code}}}
    except ConversionTimeout as exc:
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {"code": -32002, "message": str(exc), "data": {"code": "CONVERSION_TIMEOUT", "stage": exc.stage}},
        }
    except Exception as exc:
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32001, "message": str(exc)}}


def run_stdio() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        response = _handle(json.loads(line))
        if response is not None:
            print(json.dumps(response, ensure_ascii=False, sort_keys=True), flush=True)
