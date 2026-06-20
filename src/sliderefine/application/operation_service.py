from __future__ import annotations

from typing import Any

from sliderefine.domain.document import ValidationError, base_node, clone_document, make_bounds, stable_id, validate_document


class OperationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _node(document: dict[str, Any], node_id: str) -> dict[str, Any]:
    try:
        return document["nodes"][node_id]
    except KeyError as exc:
        raise OperationError("NODE_NOT_FOUND", f"Node not found: {node_id}") from exc


def _changed_nodes(op: dict[str, Any]) -> list[str]:
    if "nodeId" in op:
        return [op["nodeId"]]
    return list(op.get("nodeIds", []))


def _apply_one(document: dict[str, Any], op: dict[str, Any]) -> list[str]:
    op_type = op.get("type")
    operation_id = op.get("operationId", "")
    changed = _changed_nodes(op)

    if op_type == "set_text":
        node = _node(document, op["nodeId"])
        if node.get("type") != "text":
            raise OperationError("INVALID_NODE_TYPE", "set_text requires a text node")
        node["text"] = str(op.get("text", ""))
        node.setdefault("recognition", {})["normalizedText"] = node["text"]
        return changed

    if op_type == "translate":
        dx = float(op.get("dx", 0))
        dy = float(op.get("dy", 0))
        for node_id in op.get("nodeIds", []):
            node = _node(document, node_id)
            node["transform"][4] = float(node["transform"][4]) + dx
            node["transform"][5] = float(node["transform"][5]) + dy
        return changed

    if op_type == "resize":
        node = _node(document, op["nodeId"])
        node["bounds"]["width"] = float(op["width"])
        node["bounds"]["height"] = float(op["height"])
        return changed

    if op_type == "rotate":
        node = _node(document, op["nodeId"])
        node.setdefault("extensions", {})["rotationDegrees"] = float(op.get("degrees", 0))
        return changed

    if op_type == "set_transform":
        node = _node(document, op["nodeId"])
        transform = op.get("transform")
        if not isinstance(transform, list) or len(transform) != 6:
            raise OperationError("INVALID_TRANSFORM", "transform must be a 6 item affine matrix")
        node["transform"] = [float(value) for value in transform]
        return changed

    if op_type == "set_fill":
        node = _node(document, op["nodeId"])
        node["fill"] = {"type": "solid", "color": op["color"], "opacity": float(op.get("opacity", 1))}
        if node.get("type") == "text":
            node.setdefault("style", {})["fill"] = node["fill"]
        return changed

    if op_type == "set_stroke":
        node = _node(document, op["nodeId"])
        node["stroke"] = {"type": "solid", "color": op["color"], "width": float(op.get("width", 1))}
        return changed

    if op_type == "set_opacity":
        _node(document, op["nodeId"])["opacity"] = float(op["opacity"])
        return changed

    if op_type == "set_visibility":
        _node(document, op["nodeId"])["visible"] = bool(op["visible"])
        return changed

    if op_type == "set_locked":
        _node(document, op["nodeId"])["locked"] = bool(op["locked"])
        return changed

    if op_type == "set_name":
        _node(document, op["nodeId"])["name"] = str(op["name"])
        return changed

    if op_type == "reorder":
        parent = document["slides"].get(op.get("parentId")) or document["nodes"].get(op.get("parentId"))
        if parent is None:
            raise OperationError("PARENT_NOT_FOUND", "reorder parent not found")
        node_id = op["nodeId"]
        children = parent.get("children", [])
        if node_id not in children:
            raise OperationError("NODE_NOT_IN_PARENT", "node is not a child of parent")
        children.remove(node_id)
        children.insert(max(0, min(len(children), int(op.get("index", len(children))))), node_id)
        return [node_id]

    if op_type == "duplicate":
        source = _node(document, op["nodeId"])
        parent_id = source["parentId"]
        parent = document["slides"].get(parent_id) or document["nodes"][parent_id]
        new_node = clone_document(source)
        new_id = op.get("newNodeId") or stable_id("node", op.get("operationId", ""), source["id"], len(document["nodes"]))
        new_node["id"] = new_id
        new_node["name"] = f"{source.get('name', source['id'])} copy"
        new_node["transform"][4] = float(new_node["transform"][4]) + 16
        new_node["transform"][5] = float(new_node["transform"][5]) + 16
        document["nodes"][new_id] = new_node
        parent.setdefault("children", []).append(new_id)
        return [new_id]

    if op_type == "group":
        node_ids = list(op.get("nodeIds", []))
        if not node_ids:
            raise OperationError("EMPTY_GROUP", "group requires nodeIds")
        first = _node(document, node_ids[0])
        parent_id = first["parentId"]
        parent = document["slides"].get(parent_id) or document["nodes"].get(parent_id)
        if parent is None:
            raise OperationError("PARENT_NOT_FOUND", "group parent not found")
        if any(_node(document, node_id)["parentId"] != parent_id for node_id in node_ids):
            raise OperationError("MIXED_PARENT", "group requires nodes with the same parent")
        group_id = op.get("groupId") or stable_id("group", operation_id, ",".join(node_ids))
        group_node = base_node(group_id, "group", op.get("name", "Group"), parent_id, make_bounds(0, 0, 0, 0))
        group_node["children"] = []
        children = parent.setdefault("children", [])
        insert_at = min(children.index(node_id) for node_id in node_ids if node_id in children)
        for node_id in node_ids:
            if node_id in children:
                children.remove(node_id)
            document["nodes"][node_id]["parentId"] = group_id
            group_node["children"].append(node_id)
        children.insert(insert_at, group_id)
        document["nodes"][group_id] = group_node
        return [group_id, *node_ids]

    if op_type == "ungroup":
        group = _node(document, op["nodeId"])
        if group.get("type") != "group":
            raise OperationError("INVALID_NODE_TYPE", "ungroup requires a group node")
        parent_id = group["parentId"]
        parent = document["slides"].get(parent_id) or document["nodes"].get(parent_id)
        children = parent.setdefault("children", [])
        index = children.index(group["id"]) if group["id"] in children else len(children)
        if group["id"] in children:
            children.remove(group["id"])
        released = list(group.get("children", []))
        for offset, child_id in enumerate(released):
            document["nodes"][child_id]["parentId"] = parent_id
            children.insert(index + offset, child_id)
        del document["nodes"][group["id"]]
        return [group["id"], *released]

    if op_type == "delete":
        deleted: list[str] = []
        for node_id in op.get("nodeIds", []):
            node = document["nodes"].pop(node_id, None)
            if not node:
                continue
            parent = document["slides"].get(node["parentId"]) or document["nodes"].get(node["parentId"])
            if parent and node_id in parent.get("children", []):
                parent["children"].remove(node_id)
            deleted.append(node_id)
        return deleted

    if op_type == "replace_asset":
        node = _node(document, op["nodeId"])
        if node.get("type") != "image":
            raise OperationError("INVALID_NODE_TYPE", "replace_asset requires an image node")
        if op["assetId"] not in document["assets"]:
            raise OperationError("MISSING_ASSET", "replacement asset is not in the document")
        node["assetId"] = op["assetId"]
        return changed

    raise OperationError("UNKNOWN_OPERATION", f"Unknown operation type: {op_type}")


def apply_transaction(document: dict[str, Any], transaction: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    operation_id = transaction.get("operationId")
    if not operation_id:
        raise OperationError("MISSING_OPERATION_ID", "operationId is required")

    for log_entry in document.get("operationLog", []):
        if log_entry.get("operationId") == operation_id:
            return document, log_entry["result"]

    expected = int(transaction.get("expectedRevision", -1))
    if expected != int(document["revision"]):
        raise OperationError("REVISION_CONFLICT", f"Expected revision {expected}, found {document['revision']}")

    working = clone_document(document)
    changed: list[str] = []
    for op in transaction.get("operations", []):
        op = {**op, "operationId": operation_id}
        changed.extend(_apply_one(working, op))

    errors = validate_document(working)
    if errors:
        raise ValidationError(str(errors[:3]))

    previous = int(document["revision"])
    result = {
        "status": "ok",
        "previousRevision": previous,
        "revision": previous if transaction.get("dryRun") else previous + 1,
        "changedNodeIds": sorted(set(changed)),
        "warnings": [],
        "inverseOperations": [],
    }
    if transaction.get("dryRun"):
        return document, result

    working["revision"] = previous + 1
    working.setdefault("operationLog", []).append({"operationId": operation_id, "transaction": transaction, "result": result})
    return working, result
