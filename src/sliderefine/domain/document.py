from __future__ import annotations

import copy
import hashlib
import math
import re
import uuid
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "1.0.0"
MIGRATABLE_SCHEMA_VERSIONS = {"0.9.0"}
MAX_DOCUMENT_NODES = 5000
MAX_GROUP_DEPTH = 32
MAX_PATH_COMMANDS = 20000
MAX_PATH_CONTROL_POINTS = 40000
MAX_SLIDE_DIMENSION = 32768
MAX_SLIDE_PIXELS = 100_000_000
MAX_NODE_DIMENSION = 32768
ACTIVE_ASSET_MIME_TYPES = {"image/svg+xml"}


class DocumentError(ValueError):
    code = "DOCUMENT_ERROR"


class ValidationError(DocumentError):
    code = "VALIDATION_FAILED"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def stable_id(prefix: str, *parts: object) -> str:
    name = "|".join(str(part) for part in parts)
    digest = uuid.uuid5(uuid.NAMESPACE_URL, f"sliderefine:{name}")
    return f"{prefix}-{digest.hex[:16]}"


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def new_document(source_path: Path, source_kind: str, source_sha256: str) -> dict[str, Any]:
    doc_id = stable_id("doc", source_sha256, source_kind)
    return {
        "schemaVersion": SCHEMA_VERSION,
        "documentId": doc_id,
        "revision": 1,
        "source": {
            "kind": source_kind,
            "uri": source_path.resolve().as_uri(),
            "digest": f"sha256:{source_sha256}",
        },
        "slides": {},
        "nodes": {},
        "assets": {},
        "diagnostics": [],
        "operationLog": [],
    }


def solid(color: str | None, opacity: float = 1.0) -> dict[str, Any] | None:
    if not color:
        return None
    return {"type": "solid", "color": color, "opacity": opacity}


def make_bounds(x: float, y: float, width: float, height: float) -> dict[str, float]:
    return {
        "x": float(x),
        "y": float(y),
        "width": float(width),
        "height": float(height),
    }


def base_node(
    node_id: str,
    node_type: str,
    name: str,
    parent_id: str,
    bounds: dict[str, float],
    *,
    confidence: float | None = None,
    stage: str = "legacy_adapter",
) -> dict[str, Any]:
    return {
        "id": node_id,
        "type": node_type,
        "name": name,
        "parentId": parent_id,
        "transform": [1, 0, 0, 1, bounds["x"], bounds["y"]],
        "opacity": 1.0,
        "visible": True,
        "locked": False,
        "bounds": make_bounds(0, 0, bounds["width"], bounds["height"]),
        "provenance": {
            "stage": stage,
            "engine": "legacy_pipeline",
            "sourceRegionId": node_id,
            "confidence": confidence if confidence is not None else 1.0,
        },
        "extensions": {},
    }


def add_slide(document: dict[str, Any], slide_id: str, name: str, width: float, height: float) -> None:
    document["slides"][slide_id] = {
        "id": slide_id,
        "name": name,
        "width": float(width),
        "height": float(height),
        "children": [],
    }


def add_node(document: dict[str, Any], node: dict[str, Any]) -> None:
    parent_id = node["parentId"]
    if parent_id not in document["slides"] and parent_id not in document["nodes"]:
        raise ValidationError(f"Parent not found for node {node['id']}: {parent_id}")
    document["nodes"][node["id"]] = node
    parent = document["slides"].get(parent_id) or document["nodes"][parent_id]
    parent.setdefault("children", []).append(node["id"])


def clone_document(document: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(document)


def migrate_document(document: dict[str, Any]) -> dict[str, Any]:
    migrated = clone_document(document)
    schema_version = migrated.get("schemaVersion")
    if schema_version == SCHEMA_VERSION:
        migrated.setdefault("diagnostics", [])
        migrated.setdefault("operationLog", [])
        return migrated
    if schema_version not in MIGRATABLE_SCHEMA_VERSIONS:
        return migrated

    migrated["schemaVersion"] = SCHEMA_VERSION
    migrated.setdefault("diagnostics", [])
    migrated.setdefault("operationLog", [])
    for slide in migrated.get("slides", {}).values():
        slide.setdefault("children", [])
    for asset_id, asset in migrated.get("assets", {}).items():
        asset.setdefault("id", asset_id)
    for node_id, node in migrated.get("nodes", {}).items():
        bounds = node.setdefault("bounds", make_bounds(0, 0, 0, 0))
        node.setdefault("id", node_id)
        node.setdefault("name", node_id)
        node.setdefault("transform", [1, 0, 0, 1, float(bounds.get("x", 0)), float(bounds.get("y", 0))])
        node.setdefault("opacity", 1.0)
        node.setdefault("visible", True)
        node.setdefault("locked", False)
        node.setdefault("extensions", {})
        node.setdefault(
            "provenance",
            {
                "stage": "migration",
                "engine": "sliderefine",
                "sourceRegionId": node_id,
                "confidence": 1.0,
            },
        )
    return migrated


def matrix_is_valid(matrix: object) -> bool:
    if not isinstance(matrix, list) or len(matrix) != 6:
        return False
    return all(isinstance(value, (int, float)) and math.isfinite(float(value)) for value in matrix)


def path_command_count(document: dict[str, Any]) -> int:
    return sum(
        len(re.findall(r"[AaCcHhLlMmQqSsTtVvZz]", str(node.get("d", ""))))
        for node in document.get("nodes", {}).values()
        if node.get("type") == "path"
    )


def path_control_point_count(document: dict[str, Any]) -> int:
    control_points_by_command = {"C": 2, "S": 1, "Q": 1, "T": 1}
    return sum(
        sum(control_points_by_command.get(command.upper(), 0) for command in re.findall(r"[AaCcHhLlMmQqSsTtVvZz]", str(node.get("d", ""))))
        for node in document.get("nodes", {}).values()
        if node.get("type") == "path"
    )


def max_group_depth(document: dict[str, Any]) -> int:
    nodes = document.get("nodes", {})

    def depth(node_id: str, seen: set[str]) -> int:
        if node_id in seen:
            return MAX_GROUP_DEPTH + 1
        node = nodes.get(node_id)
        if not node or node.get("type") != "group":
            return 0
        child_depths = [depth(child_id, {*seen, node_id}) for child_id in node.get("children", [])]
        return 1 + (max(child_depths) if child_depths else 0)

    roots: list[str] = []
    for slide in document.get("slides", {}).values():
        roots.extend(slide.get("children", []))
    return max((depth(node_id, set()) for node_id in roots), default=0)


def validate_document(document: dict[str, Any]) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    required = {"schemaVersion", "documentId", "revision", "source", "slides", "nodes", "assets"}
    missing = sorted(required.difference(document))
    for key in missing:
        errors.append({"code": "MISSING_FIELD", "path": key})

    if errors:
        return errors

    if document["schemaVersion"] != SCHEMA_VERSION:
        errors.append({"code": "UNSUPPORTED_SCHEMA_VERSION", "path": "schemaVersion"})
    if not isinstance(document["revision"], int) or document["revision"] < 1:
        errors.append({"code": "INVALID_REVISION", "path": "revision"})
    extra_top_level = sorted(set(document).difference(required | {"diagnostics", "operationLog"}))
    for key in extra_top_level:
        errors.append({"code": "UNKNOWN_FIELD", "path": key})
    if len(document.get("nodes", {})) > MAX_DOCUMENT_NODES:
        errors.append(
            {
                "code": "DOCUMENT_TOO_COMPLEX",
                "path": "nodes",
                "limit": MAX_DOCUMENT_NODES,
                "actual": len(document.get("nodes", {})),
            }
        )
    depth = max_group_depth(document)
    if depth > MAX_GROUP_DEPTH:
        errors.append({"code": "GROUP_DEPTH_LIMIT", "path": "nodes", "limit": MAX_GROUP_DEPTH, "actual": depth})
    commands = path_command_count(document)
    if commands > MAX_PATH_COMMANDS:
        errors.append({"code": "PATH_COMMAND_LIMIT", "path": "nodes", "limit": MAX_PATH_COMMANDS, "actual": commands})
    control_points = path_control_point_count(document)
    if control_points > MAX_PATH_CONTROL_POINTS:
        errors.append(
            {
                "code": "PATH_CONTROL_POINT_LIMIT",
                "path": "nodes",
                "limit": MAX_PATH_CONTROL_POINTS,
                "actual": control_points,
            }
        )

    for slide_id, slide in document.get("slides", {}).items():
        for field in ("width", "height"):
            value = slide.get(field)
            if not isinstance(value, (int, float)) or not math.isfinite(float(value)) or value <= 0:
                errors.append({"code": "INVALID_SLIDE_SIZE", "path": f"slides.{slide_id}.{field}"})
            elif float(value) > MAX_SLIDE_DIMENSION:
                errors.append(
                    {
                        "code": "SLIDE_DIMENSION_LIMIT",
                        "path": f"slides.{slide_id}.{field}",
                        "limit": MAX_SLIDE_DIMENSION,
                        "actual": float(value),
                    }
                )
        width = slide.get("width", 0)
        height = slide.get("height", 0)
        if isinstance(width, (int, float)) and isinstance(height, (int, float)) and math.isfinite(float(width)) and math.isfinite(float(height)):
            pixels = float(width) * float(height)
            if pixels > MAX_SLIDE_PIXELS:
                errors.append(
                    {
                        "code": "SLIDE_PIXEL_LIMIT",
                        "path": f"slides.{slide_id}",
                        "limit": MAX_SLIDE_PIXELS,
                        "actual": pixels,
                    }
                )
        for child_id in slide.get("children", []):
            if child_id not in document["nodes"]:
                errors.append({"code": "MISSING_CHILD_NODE", "path": f"slides.{slide_id}.children"})

    for asset_id, asset in document.get("assets", {}).items():
        mime_type = str(asset.get("mimeType", "")).lower()
        if mime_type in ACTIVE_ASSET_MIME_TYPES:
            errors.append({"code": "ACTIVE_ASSET_MIME_TYPE", "path": f"assets.{asset_id}.mimeType", "mimeType": mime_type})

    for node_id, node in document.get("nodes", {}).items():
        bounds = node.get("bounds", {})
        for field in ("x", "y", "width", "height"):
            value = bounds.get(field)
            if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                errors.append({"code": "INVALID_BOUNDS", "path": f"nodes.{node_id}.bounds.{field}"})
            elif field in {"width", "height"} and float(value) > MAX_NODE_DIMENSION:
                errors.append(
                    {
                        "code": "NODE_DIMENSION_LIMIT",
                        "path": f"nodes.{node_id}.bounds.{field}",
                        "limit": MAX_NODE_DIMENSION,
                        "actual": float(value),
                    }
                )
        if float(bounds.get("width", 0)) < 0 or float(bounds.get("height", 0)) < 0:
            errors.append({"code": "NEGATIVE_BOUNDS", "path": f"nodes.{node_id}.bounds"})
        if not matrix_is_valid(node.get("transform")):
            errors.append({"code": "INVALID_TRANSFORM", "path": f"nodes.{node_id}.transform"})
        parent_id = node.get("parentId")
        if parent_id not in document["slides"] and parent_id not in document["nodes"]:
            errors.append({"code": "MISSING_PARENT", "path": f"nodes.{node_id}.parentId"})
        if node.get("type") == "image" and node.get("assetId") not in document["assets"]:
            errors.append({"code": "MISSING_ASSET", "path": f"nodes.{node_id}.assetId"})

    return errors
