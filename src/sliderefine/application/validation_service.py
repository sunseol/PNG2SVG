from __future__ import annotations

from typing import Any

from sliderefine.domain.document import max_group_depth, path_command_count, path_control_point_count, validate_document


def validation_report(document: dict[str, Any]) -> dict[str, Any]:
    errors = validate_document(document)
    return {
        "status": "ok" if not errors else "error",
        "errors": errors,
        "summary": {
            "slides": len(document.get("slides", {})),
            "nodes": len(document.get("nodes", {})),
            "assets": len(document.get("assets", {})),
            "revision": document.get("revision"),
            "maxGroupDepth": max_group_depth(document),
            "pathCommandCount": path_command_count(document),
            "pathControlPointCount": path_control_point_count(document),
        },
    }
