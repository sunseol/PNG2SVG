from __future__ import annotations

from pathlib import Path

from sliderefine.domain.document import (
    MAX_DOCUMENT_NODES,
    MAX_GROUP_DEPTH,
    MAX_PATH_COMMANDS,
    MAX_PATH_CONTROL_POINTS,
    add_node,
    add_slide,
    base_node,
    make_bounds,
    new_document,
    validate_document,
)


def make_document() -> dict:
    document = new_document(Path("fixture.png"), "image", "0" * 64)
    add_slide(document, "slide-001", "Slide", 100, 100)
    return document


def test_document_node_count_limit_rejected():
    document = make_document()
    for index in range(MAX_DOCUMENT_NODES + 1):
        node = base_node(f"rect-{index}", "rect", f"Rect {index}", "slide-001", make_bounds(0, 0, 1, 1))
        node["fill"] = {"type": "solid", "color": "#000000", "opacity": 1}
        add_node(document, node)
    errors = validate_document(document)
    assert any(error["code"] == "DOCUMENT_TOO_COMPLEX" for error in errors)


def test_document_group_depth_limit_rejected():
    document = make_document()
    parent_id = "slide-001"
    for index in range(MAX_GROUP_DEPTH + 1):
        group = base_node(f"group-{index}", "group", f"Group {index}", parent_id, make_bounds(0, 0, 1, 1))
        group["children"] = []
        add_node(document, group)
        parent_id = group["id"]
    errors = validate_document(document)
    assert any(error["code"] == "GROUP_DEPTH_LIMIT" for error in errors)


def test_document_path_command_limit_rejected():
    document = make_document()
    path = base_node("path-heavy", "path", "Path", "slide-001", make_bounds(0, 0, 10, 10))
    path.update(
        {
            "d": "M 0 0 " + " ".join("L 1 1" for _ in range(MAX_PATH_COMMANDS + 1)),
            "fill": {"type": "solid", "color": "#000000", "opacity": 1},
            "fillRule": "evenodd",
        }
    )
    add_node(document, path)
    errors = validate_document(document)
    assert any(error["code"] == "PATH_COMMAND_LIMIT" for error in errors)


def test_document_path_control_point_limit_rejected():
    document = make_document()
    path = base_node("path-cubic", "path", "Path", "slide-001", make_bounds(0, 0, 10, 10))
    path.update(
        {
            "d": "M 0 0 " + " ".join("C 1 1 2 2 3 3" for _ in range((MAX_PATH_CONTROL_POINTS // 2) + 1)),
            "fill": {"type": "solid", "color": "#000000", "opacity": 1},
            "fillRule": "evenodd",
        }
    )
    add_node(document, path)
    errors = validate_document(document)
    assert any(error["code"] == "PATH_CONTROL_POINT_LIMIT" for error in errors)
