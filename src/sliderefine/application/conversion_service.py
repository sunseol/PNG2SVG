from __future__ import annotations

import argparse
import io
import mimetypes
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image

from sliderefine import legacy_pipeline as legacy
from sliderefine.domain.document import (
    add_node,
    add_slide,
    base_node,
    file_digest,
    make_bounds,
    new_document,
    sha256_bytes,
    solid,
    stable_id,
)


@dataclass(frozen=True)
class ConversionOptions:
    colors: int = 12
    min_area: float = 24.0
    simplify: float = 1.5
    blur: float = 0.6
    alpha_threshold: int = 8
    merge_distance: float = 18.0
    text_handling: str = "exclude"
    text_padding: int = 6
    skip_text_detection: bool = False
    ocr_engine: str = "none"
    deterministic: bool = True
    max_slides: int = 100
    max_image_pixels: int = 100_000_000
    max_source_bytes: int = 256 * 1024 * 1024
    timeout_ms: int | None = None

    def to_legacy_namespace(self) -> argparse.Namespace:
        return argparse.Namespace(
            colors=self.colors,
            min_area=self.min_area,
            simplify=self.simplify,
            blur=self.blur,
            alpha_threshold=self.alpha_threshold,
            merge_distance=self.merge_distance,
            text_handling=self.text_handling,
            text_padding=self.text_padding,
            skip_text_detection=self.skip_text_detection,
            ocr_engine=self.ocr_engine,
        )


@dataclass(frozen=True)
class ConversionResult:
    document: dict[str, Any]
    rendered_slides: list[dict[str, Any]]
    warnings: list[dict[str, Any]]
    metrics: dict[str, Any]


class ConversionTimeout(TimeoutError):
    def __init__(self, timeout_ms: int, stage: str) -> None:
        super().__init__(f"Conversion timed out after {timeout_ms} ms during {stage}")
        self.timeout_ms = timeout_ms
        self.stage = stage


def _deadline(start: float, options: ConversionOptions) -> float | None:
    if options.timeout_ms is None:
        return None
    return start + (options.timeout_ms / 1000.0)


def _check_timeout(deadline: float | None, options: ConversionOptions, stage: str) -> None:
    if deadline is not None and options.timeout_ms is not None and time.perf_counter() >= deadline:
        raise ConversionTimeout(options.timeout_ms, stage)


def _source_kind(path: Path) -> str:
    if path.is_dir():
        return "directory"
    if path.suffix.lower() == ".pptx":
        return "pptx"
    return "image"


def _png_bytes(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.convert("RGBA").save(buffer, format="PNG")
    return buffer.getvalue()


def _add_asset(document: dict[str, Any], data: bytes, mime_type: str) -> str:
    digest = sha256_bytes(data)
    extension = mimetypes.guess_extension(mime_type) or ".bin"
    asset_id = f"sha256:{digest}"
    document["assets"][asset_id] = {
        "id": asset_id,
        "mimeType": mime_type,
        "byteLength": len(data),
        "sha256": digest,
        "path": f"assets/sha256-{digest}{extension}",
        "_bytes": data,
    }
    return asset_id


def _path_nodes(document: dict[str, Any], slide: dict[str, Any], source_sha: str) -> None:
    slide_id = slide["id"]
    for element in slide.get("elements", []):
        vector_width = max(1.0, float(element["vector"]["width"]))
        vector_height = max(1.0, float(element["vector"]["height"]))
        scale_x = float(element["width"]) / vector_width
        scale_y = float(element["height"]) / vector_height
        for layer in element["vector"]["layers"]:
            for index, path_data in enumerate(layer["paths"], start=1):
                node_id = stable_id(
                    "path",
                    source_sha,
                    slide_id,
                    element["name"],
                    layer["id"],
                    index,
                    path_data,
                )
                bounds = make_bounds(float(element["x"]), float(element["y"]), float(element["width"]), float(element["height"]))
                node = base_node(
                    node_id,
                    "path",
                    f"{layer['id']}-{index}",
                    slide_id,
                    bounds,
                    stage="shape_reconstruction",
                )
                node.update(
                    {
                        "d": path_data,
                        "fillRule": "evenodd",
                        "fill": solid(layer.get("color")),
                        "stroke": None,
                        "transform": [scale_x, 0, 0, scale_y, float(element["x"]), float(element["y"])],
                        "extensions": {
                            "legacy": {
                                "layerId": layer["id"],
                                "role": layer.get("role"),
                                "pixelCount": layer.get("pixel_count"),
                            }
                        },
                    }
                )
                add_node(document, node)


def _text_nodes(document: dict[str, Any], slide: dict[str, Any], source_sha: str) -> None:
    for candidate in slide.get("text_candidates", []):
        node_id = stable_id(
            "text",
            source_sha,
            slide["id"],
            candidate.get("id"),
            candidate.get("x"),
            candidate.get("y"),
            candidate.get("text") or "",
        )
        bounds = make_bounds(
            float(candidate.get("x") or 0),
            float(candidate.get("y") or 0),
            float(candidate.get("width") or 0),
            float(candidate.get("height") or 0),
        )
        node = base_node(
            node_id,
            "text",
            candidate.get("text") or candidate.get("id") or "Text",
            slide["id"],
            bounds,
            confidence=float(candidate.get("confidence") or 0),
            stage="text_reconstruction",
        )
        style = candidate.get("text_style") or {}
        node.update(
            {
                "text": candidate.get("text") or "",
                "style": {
                    "fontFamily": style.get("font_family", "Arial"),
                    "fontSize": float(style.get("font_size") or max(12.0, bounds["height"] * 0.6)),
                    "fontWeight": int(style.get("font_weight") or 400),
                    "lineHeight": float(style.get("line_height") or 1.2),
                    "letterSpacing": 0,
                    "align": style.get("align", "left"),
                    "fill": solid(style.get("fill") or "#111111"),
                },
                "layout": {
                    "width": bounds["width"],
                    "height": bounds["height"],
                    "overflow": "visible",
                },
                "recognition": {
                    "rawText": candidate.get("raw_text") or candidate.get("text") or "",
                    "normalizedText": candidate.get("text") or "",
                    "language": candidate.get("language") or "und",
                    "confidence": float(candidate.get("confidence") or 0),
                    "status": candidate.get("text_status") or "needs_review",
                    "alternatives": [],
                },
            }
        )
        add_node(document, node)


def _raster_nodes(document: dict[str, Any], slide: dict[str, Any], source_sha: str) -> None:
    reference = legacy.composite_slide_reference(slide)
    reference_bytes = _png_bytes(reference)
    reference_asset_id = _add_asset(document, reference_bytes, "image/png")
    reference_id = stable_id("image", source_sha, slide["id"], "reference")
    reference_node = base_node(
        reference_id,
        "image",
        "Original raster reference",
        slide["id"],
        make_bounds(0, 0, float(slide["width"]), float(slide["height"])),
        stage="source_loader",
    )
    reference_node.update(
        {
            "assetId": reference_asset_id,
            "crop": None,
            "mask": None,
            "reason": "original_overlay",
            "locked": True,
            "opacity": 0.0,
        }
    )
    add_node(document, reference_node)

    for region in slide.get("raster_regions", []):
        image = region.get("image")
        if image is None:
            continue
        data = _png_bytes(image)
        asset_id = _add_asset(document, data, "image/png")
        node_id = stable_id("image", source_sha, slide["id"], region.get("id"), asset_id)
        bounds = make_bounds(
            float(region.get("x") or 0),
            float(region.get("y") or 0),
            float(region.get("width") or 0),
            float(region.get("height") or 0),
        )
        node = base_node(node_id, "image", region.get("id") or "Raster region", slide["id"], bounds, stage="raster_fallback")
        node.update({"assetId": asset_id, "crop": None, "mask": None, "reason": "raster_fallback"})
        add_node(document, node)


def _document_from_rendered(source_path: Path, rendered_slides: list[dict[str, Any]]) -> dict[str, Any]:
    source_sha = file_digest(source_path) if source_path.is_file() else sha256_bytes(str(source_path).encode("utf-8"))
    document = new_document(source_path, _source_kind(source_path), source_sha)
    for slide in rendered_slides:
        add_slide(document, slide["id"], slide.get("name") or slide["id"], float(slide["width"]), float(slide["height"]))
        _raster_nodes(document, slide, source_sha)
        _path_nodes(document, slide, source_sha)
        _text_nodes(document, slide, source_sha)
        document["diagnostics"].append(
            {
                "slideId": slide["id"],
                "vectorLayerCount": slide.get("vector_layer_count", 0),
                "vectorPathCount": slide.get("vector_path_count", 0),
                "analysis": slide.get("analysis", {}),
            }
        )
    return document


def convert(source: Path | str, options: ConversionOptions | None = None) -> ConversionResult:
    start = time.perf_counter()
    source_path = Path(source).expanduser().resolve()
    if not source_path.exists():
        raise FileNotFoundError(source_path)
    options = options or ConversionOptions()
    deadline = _deadline(start, options)
    _check_timeout(deadline, options, "startup")
    if source_path.is_file() and source_path.stat().st_size > options.max_source_bytes:
        raise ValueError(
            f"Source file is {source_path.stat().st_size} bytes, limit is {options.max_source_bytes}"
        )
    legacy_options = options.to_legacy_namespace()
    source_slides = legacy.load_source_slides(source_path)
    _check_timeout(deadline, options, "source loading")
    if len(source_slides) > options.max_slides:
        raise ValueError(f"Source has {len(source_slides)} slides, limit is {options.max_slides}")
    for slide in source_slides:
        pixels = float(slide["width"]) * float(slide["height"])
        if pixels > options.max_image_pixels:
            raise ValueError(f"Slide {slide['id']} exceeds max image pixels")
        _check_timeout(deadline, options, f"input validation for {slide['id']}")

    rendered_slides = []
    for slide in source_slides:
        _check_timeout(deadline, options, f"rendering {slide['id']}")
        rendered_slides.append(legacy.render_slide(slide=slide, options=legacy_options))
        _check_timeout(deadline, options, f"rendering {slide['id']}")
    document = _document_from_rendered(source_path, rendered_slides)
    _check_timeout(deadline, options, "document assembly")
    warnings = [
        {
            "code": "TEXT_LOW_CONFIDENCE",
            "nodeId": node["id"],
            "confidence": node.get("recognition", {}).get("confidence"),
        }
        for node in document["nodes"].values()
        if node.get("type") == "text" and float(node.get("recognition", {}).get("confidence") or 0) < 0.8
    ]
    return ConversionResult(
        document=document,
        rendered_slides=rendered_slides,
        warnings=warnings,
        metrics={"durationMs": int((time.perf_counter() - start) * 1000)},
    )
