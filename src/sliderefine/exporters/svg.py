from __future__ import annotations

import base64
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from sliderefine.exporters.srf import read_asset_bytes


def fmt(value: float) -> str:
    text = f"{float(value):.2f}"
    return text.rstrip("0").rstrip(".") or "0"


def _attr(value: object) -> str:
    return escape(str(value), {'"': "&quot;"})


def _matrix(transform: list[float]) -> str:
    return "matrix(" + " ".join(fmt(float(value)) for value in transform) + ")"


def _transform(node: dict[str, Any]) -> str:
    transform = _matrix(node.get("transform", [1, 0, 0, 1, 0, 0]))
    degrees = float(node.get("extensions", {}).get("rotationDegrees", 0) or 0)
    if degrees == 0:
        return transform
    bounds = node.get("bounds", {})
    cx = float(bounds.get("width", 0)) / 2
    cy = float(bounds.get("height", 0)) / 2
    return f"{transform} rotate({fmt(degrees)} {fmt(cx)} {fmt(cy)})"


def _fill(node: dict[str, Any]) -> str:
    fill = node.get("fill")
    if isinstance(fill, dict) and fill.get("type") == "solid":
        return str(fill.get("color", "none"))
    style = node.get("style", {})
    text_fill = style.get("fill") if isinstance(style, dict) else None
    if isinstance(text_fill, dict):
        return str(text_fill.get("color", "#111111"))
    return "none"


def _image_href(document: dict[str, Any], node: dict[str, Any], archive_path: Path | None) -> str:
    asset = document["assets"][node["assetId"]]
    data = read_asset_bytes(document, node["assetId"], archive_path)
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{asset['mimeType']};base64,{encoded}"


def _render_node(
    document: dict[str, Any],
    node: dict[str, Any],
    *,
    archive_path: Path | None,
    include_guides: bool,
) -> list[str]:
    if not node.get("visible", True):
        return []
    opacity = fmt(float(node.get("opacity", 1.0)))
    transform = _transform(node)
    bounds = node.get("bounds", {})
    node_id = _attr(node["id"])
    common = f'id="{node_id}" transform="{transform}" opacity="{opacity}" data-node-type="{node["type"]}"'
    node_type = node.get("type")

    if node_type == "group":
        lines = [f'  <g {common}>']
        for child_id in node.get("children", []):
            lines.extend(
                "  " + line
                for line in _render_node(
                    document,
                    document["nodes"][child_id],
                    archive_path=archive_path,
                    include_guides=include_guides,
                )
            )
        lines.append("  </g>")
        return lines

    if node_type == "path":
        stroke = node.get("stroke")
        stroke_attrs = ""
        if isinstance(stroke, dict) and stroke.get("type") == "solid":
            stroke_attrs = f' stroke="{_attr(stroke.get("color"))}"'
        return [
            f'  <path {common} d="{_attr(node.get("d", ""))}" fill="{_attr(_fill(node))}" '
            f'fill-rule="{_attr(node.get("fillRule", "evenodd"))}"{stroke_attrs}/>'
        ]

    if node_type == "rect":
        return [
            f'  <rect {common} x="{fmt(bounds.get("x", 0))}" y="{fmt(bounds.get("y", 0))}" '
            f'width="{fmt(bounds.get("width", 0))}" height="{fmt(bounds.get("height", 0))}" '
            f'fill="{_attr(_fill(node))}"/>'
        ]

    if node_type == "ellipse":
        width = float(bounds.get("width", 0))
        height = float(bounds.get("height", 0))
        return [
            f'  <ellipse {common} cx="{fmt(width / 2)}" cy="{fmt(height / 2)}" '
            f'rx="{fmt(width / 2)}" ry="{fmt(height / 2)}" fill="{_attr(_fill(node))}"/>'
        ]

    if node_type == "image":
        if node.get("reason") == "original_overlay" and not include_guides and float(node.get("opacity", 0)) <= 0:
            return []
        href = _image_href(document, node, archive_path)
        return [
            f'  <image {common} width="{fmt(bounds.get("width", 0))}" '
            f'height="{fmt(bounds.get("height", 0))}" href="{href}"/>'
        ]

    if node_type == "text":
        style = node.get("style", {})
        font_size = fmt(style.get("fontSize", 16))
        font_family = _attr(style.get("fontFamily", "Arial"))
        font_weight = _attr(style.get("fontWeight", 400))
        return [
            f'  <text {common} x="0" y="{font_size}" fill="{_attr(_fill(node))}" '
            f'font-family="{font_family}" font-size="{font_size}" font-weight="{font_weight}">'
            f'{escape(node.get("text", ""))}</text>'
        ]

    if node_type == "guide" and include_guides:
        return [
            f'  <rect {common} width="{fmt(bounds.get("width", 0))}" height="{fmt(bounds.get("height", 0))}" '
            'fill="none" stroke="#ff6b00" stroke-dasharray="6 4"/>'
        ]

    return []


def build_svg(
    document: dict[str, Any],
    *,
    slide_id: str | None = None,
    archive_path: Path | None = None,
    include_guides: bool = True,
) -> str:
    if slide_id is None:
        slide_id = next(iter(document["slides"]))
    slide = document["slides"][slide_id]
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{fmt(slide["width"])}" '
            f'height="{fmt(slide["height"])}" viewBox="0 0 {fmt(slide["width"])} {fmt(slide["height"])}">'
        ),
        "  <metadata>Generated by SlideRefine</metadata>",
        f'  <g id="{_attr(slide_id)}" data-role="slide">',
    ]
    for node_id in slide.get("children", []):
        lines.extend(
            _render_node(document, document["nodes"][node_id], archive_path=archive_path, include_guides=include_guides)
        )
    lines.append("  </g>")
    lines.append("</svg>")
    return "\n".join(lines) + "\n"


def export_svg(
    document: dict[str, Any],
    output_path: Path,
    *,
    slide_id: str | None = None,
    archive_path: Path | None = None,
    include_guides: bool = True,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        build_svg(document, slide_id=slide_id, archive_path=archive_path, include_guides=include_guides),
        encoding="utf-8",
    )
