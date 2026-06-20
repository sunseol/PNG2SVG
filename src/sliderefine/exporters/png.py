from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from sliderefine.exporters.srf import read_asset_bytes


def _xy(transform: list[float]) -> tuple[float, float]:
    return float(transform[4]), float(transform[5])


def _path_points(d: str, x: float, y: float, scale: float) -> list[tuple[int, int]]:
    tokens = re.findall(r"[MLZmlz]|-?\d+(?:\.\d+)?", d)
    points: list[tuple[int, int]] = []
    index = 0
    command = ""
    while index < len(tokens):
        token = tokens[index]
        if token.upper() in {"M", "L", "Z"}:
            command = token
            index += 1
            if command.upper() == "Z":
                continue
        if command.upper() not in {"M", "L"} or index + 1 >= len(tokens):
            break
        px = float(tokens[index])
        py = float(tokens[index + 1])
        if command.islower():
            last_x, last_y = points[-1] if points else (int(round(x * scale)), int(round(y * scale)))
            points.append((int(round(last_x + px * scale)), int(round(last_y + py * scale))))
        else:
            points.append((int(round((x + px) * scale)), int(round((y + py) * scale))))
        index += 2
    return points


def _render_node(
    canvas: Image.Image,
    draw: ImageDraw.ImageDraw,
    document: dict[str, Any],
    node: dict[str, Any],
    *,
    archive_path: Path | None,
    scale: float,
    offset_x: float = 0,
    offset_y: float = 0,
) -> None:
    if not node.get("visible", True) or float(node.get("opacity", 1.0)) <= 0:
        return
    x, y = _xy(node.get("transform", [1, 0, 0, 1, 0, 0]))
    x += offset_x
    y += offset_y
    if node["type"] == "group":
        for child_id in node.get("children", []):
            _render_node(
                canvas,
                draw,
                document,
                document["nodes"][child_id],
                archive_path=archive_path,
                scale=scale,
                offset_x=x,
                offset_y=y,
            )
        return
    bounds = node.get("bounds", {})
    box = [
        int(round(x * scale)),
        int(round(y * scale)),
        int(round((x + float(bounds.get("width", 0))) * scale)),
        int(round((y + float(bounds.get("height", 0))) * scale)),
    ]
    if node["type"] == "image":
        data = read_asset_bytes(document, node["assetId"], archive_path)
        image = Image.open(io.BytesIO(data)).convert("RGBA")
        image = image.resize((max(1, box[2] - box[0]), max(1, box[3] - box[1])))
        canvas.alpha_composite(image, (box[0], box[1]))
    elif node["type"] == "rect":
        fill = node.get("fill", {}).get("color", "#000000")
        draw.rectangle(box, fill=fill)
    elif node["type"] == "ellipse":
        fill = node.get("fill", {}).get("color", "#000000")
        draw.ellipse(box, fill=fill)
    elif node["type"] == "path":
        points = _path_points(str(node.get("d", "")), x, y, scale)
        fill = node.get("fill", {}).get("color", "#000000")
        if len(points) >= 3:
            draw.polygon(points, fill=fill)
    elif node["type"] == "text":
        fill = node.get("style", {}).get("fill", {}).get("color", "#111111")
        font_size = int(round(float(node.get("style", {}).get("fontSize", 16)) * scale))
        font: Any
        try:
            font = ImageFont.truetype("arial.ttf", max(1, font_size))
        except OSError:
            font = ImageFont.load_default()
        draw.text((box[0], box[1]), node.get("text", ""), fill=fill, font=font)


def render_png(
    document: dict[str, Any],
    output_path: Path,
    *,
    slide_id: str | None = None,
    archive_path: Path | None = None,
    scale: float = 1.0,
) -> None:
    if slide_id is None:
        slide_id = next(iter(document["slides"]))
    slide = document["slides"][slide_id]
    width = max(1, int(round(float(slide["width"]) * scale)))
    height = max(1, int(round(float(slide["height"]) * scale)))
    canvas = Image.new("RGBA", (width, height), (255, 255, 255, 0))
    draw = ImageDraw.Draw(canvas)

    for node_id in slide.get("children", []):
        _render_node(canvas, draw, document, document["nodes"][node_id], archive_path=archive_path, scale=scale)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGBA").save(output_path)
