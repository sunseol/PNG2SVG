#!/usr/bin/env python
from __future__ import annotations

import argparse
import base64
import io
import json
import posixpath
import re
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

import cv2
import numpy as np
from PIL import Image, ImageFilter

PPT_NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
EMU_PER_PIXEL = 9525.0
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}
SVG_GUIDE_STROKE = "#ff6b00"
OCR_ENGINE_CHOICES = ("auto", "windows", "none")
EDITABLE_TEXT_STATUSES = {"usable", "usable_corrected", "needs_review"}
OCR_TEXT_REPLACEMENTS = (
    ("타사人", "탄산수"),
    ("타사수", "탄산수"),
    ("타산수", "탄산수"),
    ("탄사수", "탄산수"),
    ("가라엘", "카라멜"),
    ("줄길", "즐길"),
    ("마실때주의할점", "마실 때 주의할 점"),
    ("마실때 주의할점", "마실 때 주의할 점"),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Convert GPT-generated slide images into Figma-friendly vector packages. "
            "The package output separates vector regions from text candidate guides."
        )
    )
    parser.add_argument(
        "input",
        help="Input image, directory of slide images, or image-based PPTX file.",
    )
    parser.add_argument(
        "-o",
        "--output",
        help=(
            "Output directory for package mode. In svg mode, use a .svg file for a "
            "single image or a directory for multi-slide inputs."
        ),
    )
    parser.add_argument(
        "--mode",
        choices=("package", "svg"),
        default="package",
        help="Package creates a Figma handoff bundle; svg writes only SVG outputs.",
    )
    parser.add_argument(
        "--colors",
        type=int,
        default=12,
        help="Maximum number of quantized colors to keep per source image.",
    )
    parser.add_argument(
        "--min-area",
        type=float,
        default=24.0,
        help="Ignore contours smaller than this area in square pixels.",
    )
    parser.add_argument(
        "--simplify",
        type=float,
        default=1.5,
        help="Douglas-Peucker simplification epsilon in pixels.",
    )
    parser.add_argument(
        "--blur",
        type=float,
        default=0.6,
        help="Gaussian blur radius before color quantization.",
    )
    parser.add_argument(
        "--alpha-threshold",
        type=int,
        default=8,
        help="Ignore pixels with alpha below this threshold.",
    )
    parser.add_argument(
        "--merge-distance",
        type=float,
        default=18.0,
        help="Merge quantized colors whose RGB distance is below this value.",
    )
    parser.add_argument(
        "--text-handling",
        choices=("exclude", "vectorize"),
        default="exclude",
        help=(
            "exclude removes detected text candidates from vectorization and emits "
            "editable guide boxes; vectorize keeps all pixels in vector traces."
        ),
    )
    parser.add_argument(
        "--text-padding",
        type=int,
        default=6,
        help="Padding in pixels around detected text boxes before exclusion.",
    )
    parser.add_argument(
        "--skip-text-detection",
        action="store_true",
        help="Disable text candidate detection and guide generation.",
    )
    parser.add_argument(
        "--ocr-engine",
        choices=OCR_ENGINE_CHOICES,
        default="auto",
        help="OCR engine used to recover text blocks before fallback heuristics.",
    )
    return parser.parse_args()


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def emu_to_px(value: int | float) -> float:
    return float(value) / EMU_PER_PIXEL


def fmt(value: float) -> str:
    text = f"{value:.2f}"
    return text.rstrip("0").rstrip(".") or "0"


def path_id(label: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "-", label.strip()).strip("-")
    return safe or "layer"


def escape_attr(value: str) -> str:
    return escape(value, {'"': "&quot;"})


def color_hex(color: Iterable[int]) -> str:
    r, g, b = [int(channel) for channel in color]
    return f"#{r:02x}{g:02x}{b:02x}"


def is_editable_text_candidate(candidate: dict) -> bool:
    return bool(candidate.get("text")) and candidate.get("text_status") in EDITABLE_TEXT_STATUSES


def text_replacement_policy(candidate: dict) -> tuple[str, str]:
    if not is_editable_text_candidate(candidate):
        return "preserve_only", "not_editable_text"

    text = str(candidate.get("text") or "").strip()
    height = float(candidate.get("height") or 0)
    width = float(candidate.get("width") or 0)
    quality = candidate.get("text_quality")

    if candidate.get("correction_applied"):
        return "visible_review", "ocr_correction_review"
    if candidate.get("text_status") != "usable":
        return "visible_review", "non_standard_ocr_status"
    if quality is not None and float(quality) < 0.92:
        return "visible_review", "low_ocr_quality"
    if height <= 26 and (text.startswith("\u2022") or width <= 340):
        return "visible_review", "small_label_review"
    if height <= 22:
        return "visible_review", "small_text_review"
    return "visible_replace", "high_confidence_replace"


def should_remove_source_text(candidate: dict) -> bool:
    return is_editable_text_candidate(candidate)


def contour_to_svg_path(contour: np.ndarray) -> str:
    points = contour.reshape(-1, 2)
    if len(points) < 3:
        return ""
    parts = [f"M {fmt(points[0][0])} {fmt(points[0][1])}"]
    for x, y in points[1:]:
        parts.append(f"L {fmt(x)} {fmt(y)}")
    parts.append("Z")
    return " ".join(parts)


def quantize_image(
    image: Image.Image,
    colors: int,
    blur_radius: float,
    alpha_threshold: int,
) -> tuple[np.ndarray, np.ndarray]:
    rgba = image.convert("RGBA")
    if blur_radius > 0:
        rgba = rgba.filter(ImageFilter.GaussianBlur(radius=blur_radius))

    rgba_array = np.array(rgba)
    alpha_mask = rgba_array[:, :, 3] >= alpha_threshold
    white = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
    rgb = Image.alpha_composite(white, rgba).convert("RGB")
    quantized = rgb.quantize(colors=max(2, colors), method=Image.Quantize.MEDIANCUT)
    return np.array(quantized.convert("RGB")), alpha_mask


def merge_similar_colors(
    rgb_array: np.ndarray,
    alpha_mask: np.ndarray,
    merge_distance: float,
) -> np.ndarray:
    if merge_distance <= 0:
        return rgb_array

    valid_pixels = rgb_array[alpha_mask]
    if valid_pixels.size == 0:
        return rgb_array

    unique_colors, counts = np.unique(valid_pixels, axis=0, return_counts=True)
    order = np.argsort(-counts)
    canonical_colors: list[np.ndarray] = []
    mapping: dict[tuple[int, int, int], np.ndarray] = {}

    for color in unique_colors[order]:
        key = tuple(int(channel) for channel in color)
        assigned = None
        for canonical in canonical_colors:
            distance = np.linalg.norm(
                color.astype(np.float32) - canonical.astype(np.float32)
            )
            if distance <= merge_distance:
                assigned = canonical
                break
        if assigned is None:
            assigned = color.copy()
            canonical_colors.append(assigned)
        mapping[key] = assigned

    merged = rgb_array.copy()
    for source, target in mapping.items():
        if source == tuple(int(channel) for channel in target):
            continue
        mask = (
            (merged[:, :, 0] == source[0])
            & (merged[:, :, 1] == source[1])
            & (merged[:, :, 2] == source[2])
        )
        if np.any(mask):
            merged[mask] = target

    return merged


def should_merge_boxes(
    left: dict,
    right: dict,
    gap_x: int,
    gap_y: int,
) -> bool:
    left_x2 = left["x"] + left["width"]
    right_x2 = right["x"] + right["width"]
    left_y2 = left["y"] + left["height"]
    right_y2 = right["y"] + right["height"]

    horizontal_gap = max(left["x"] - right_x2, right["x"] - left_x2, 0)
    vertical_gap = max(left["y"] - right_y2, right["y"] - left_y2, 0)
    overlap_x = max(0, min(left_x2, right_x2) - max(left["x"], right["x"]))
    overlap_y = max(0, min(left_y2, right_y2) - max(left["y"], right["y"]))

    touches = horizontal_gap <= gap_x and overlap_y > 0
    stacked = vertical_gap <= gap_y and overlap_x > min(left["width"], right["width"]) * 0.2
    overlapping = overlap_x > 0 and overlap_y > 0
    return touches or stacked or overlapping


def merge_box_pair(left: dict, right: dict) -> dict:
    x1 = min(left["x"], right["x"])
    y1 = min(left["y"], right["y"])
    x2 = max(left["x"] + left["width"], right["x"] + right["width"])
    y2 = max(left["y"] + left["height"], right["y"] + right["height"])
    text_lines = []
    for box in (left, right):
        if box.get("text_lines"):
            text_lines.extend(line for line in box["text_lines"] if line)
        elif box.get("text"):
            text_lines.append(box["text"])

    text_lines = list(dict.fromkeys(text_lines))
    return {
        "x": x1,
        "y": y1,
        "width": x2 - x1,
        "height": y2 - y1,
        "confidence": round(max(left["confidence"], right["confidence"]), 3),
        "fragment_count": int(left.get("fragment_count", 1))
        + int(right.get("fragment_count", 1)),
        "source": left.get("source") if left.get("source") == right.get("source") else "mixed",
        "text": "\n".join(text_lines).strip() or None,
        "text_lines": text_lines,
    }


def merge_candidate_boxes(
    boxes: list[dict],
    image_width: int,
    image_height: int,
) -> list[dict]:
    if not boxes:
        return []

    gap_x = max(10, image_width // 80)
    gap_y = max(6, image_height // 90)
    merged = sorted(boxes, key=lambda item: (item["y"], item["x"]))

    changed = True
    while changed:
        changed = False
        next_round: list[dict] = []
        for box in merged:
            match_index = None
            for index, existing in enumerate(next_round):
                if should_merge_boxes(existing, box, gap_x=gap_x, gap_y=gap_y):
                    match_index = index
                    break
            if match_index is None:
                next_round.append(box)
            else:
                next_round[match_index] = merge_box_pair(next_round[match_index], box)
                changed = True
        merged = sorted(next_round, key=lambda item: (item["y"], item["x"]))

    finalized = []
    for index, box in enumerate(merged, start=1):
        aspect = box["width"] / max(1.0, box["height"])
        if box["height"] > image_height * 0.22 or aspect < 1.1:
            continue
        if int(box.get("fragment_count", 1)) < 2:
            continue
        finalized.append(
            {
                **box,
                "id": f"text-candidate-{index:02d}",
            }
        )
    return finalized


def detect_heuristic_text_candidates(image: Image.Image) -> list[dict]:
    rgb = np.array(image.convert("RGB"))
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    mser = cv2.MSER_create(
        min_area=max(20, int(image.width * image.height * 0.00002)),
        max_area=max(5000, int(image.width * image.height * 0.01)),
    )
    regions, _ = mser.detectRegions(gray)

    candidates = []
    seen_boxes: set[tuple[int, int, int, int]] = set()
    for region in regions:
        x, y, width, height = cv2.boundingRect(region.reshape(-1, 1, 2))
        key = (int(x), int(y), int(width), int(height))
        if key in seen_boxes:
            continue
        seen_boxes.add(key)

        area = width * height
        if area < 20 or area > image.width * image.height * 0.03:
            continue
        if width < 3 or height < 5:
            continue
        if height > image.height * 0.15:
            continue

        aspect = width / max(1.0, height)
        if aspect > 12.0 or aspect < 0.2:
            continue

        confidence = clamp(
            0.15 + min(aspect, 4.0) / 10.0 + min(area, 120.0) / 400.0,
            0.0,
            0.85,
        )
        candidates.append(
            {
                "x": int(x),
                "y": int(y),
                "width": int(width),
                "height": int(height),
                "confidence": round(confidence, 3),
                "fragment_count": 1,
                "source": "heuristic",
                "text": None,
                "text_lines": [],
            }
        )

    return merge_candidate_boxes(
        boxes=candidates,
        image_width=image.width,
        image_height=image.height,
    )


def is_meaningful_ocr_text(text: str) -> bool:
    normalized = " ".join(text.split())
    if not normalized:
        return False

    meaningful_chars = re.findall(r"[0-9A-Za-z\u3131-\u318E\uAC00-\uD7A3]", normalized)
    if len(meaningful_chars) < 2:
        return False

    non_numeric_chars = re.findall(r"[A-Za-z\u3131-\u318E\uAC00-\uD7A3]", normalized)
    if not non_numeric_chars and len(meaningful_chars) < 3:
        return False

    return True


def apply_ocr_text_corrections(text: str) -> tuple[str, bool]:
    corrected = " ".join(text.split())
    changed = False
    for source, target in OCR_TEXT_REPLACEMENTS:
        if source in corrected:
            corrected = corrected.replace(source, target)
            changed = True
    return corrected, changed


def assess_ocr_text_quality(text: str) -> tuple[str | None, float, str]:
    normalized = " ".join(text.split())
    if not normalized:
        return None, 0.0, "empty"

    allowed_punctuation = set(" .,!?-_/:%&()+#'\"[]{}<>|~")
    allowed_symbols = {"\u2022", "\u00b7", "\u2026"}
    meaningful_chars = re.findall(r"[0-9A-Za-z\u3131-\u318E\uAC00-\uD7A3]", normalized)
    if not meaningful_chars:
        return None, 0.0, "no_meaningful_chars"

    hangul_chars = re.findall(r"[\u3131-\u318E\uAC00-\uD7A3]", normalized)
    latin_chars = re.findall(r"[A-Za-z]", normalized)

    weird_chars = 0
    cjk_ideographs = 0
    for char in normalized:
        if char.isspace() or char in allowed_punctuation or char in allowed_symbols:
            continue
        if re.match(r"[0-9A-Za-z\u3131-\u318E\uAC00-\uD7A3]", char):
            continue
        if "\u4e00" <= char <= "\u9fff":
            cjk_ideographs += 1
        weird_chars += 1

    weird_ratio = weird_chars / max(1, len(normalized))
    hangul_or_latin = re.findall(r"[A-Za-z\u3131-\u318E\uAC00-\uD7A3]", normalized)
    quality = 1.0 - weird_ratio
    quality -= min(cjk_ideographs * 0.12, 0.5)
    latin_ratio = len(latin_chars) / max(1, len(meaningful_chars))

    if len(hangul_or_latin) >= 5 and weird_ratio > 0.12:
        return None, round(max(0.0, quality), 3), "noisy_text"
    if cjk_ideographs >= 2 and len(hangul_or_latin) >= 4:
        return None, round(max(0.0, quality), 3), "mixed_script_noise"
    if hangul_chars and latin_chars and len(latin_chars) >= 3 and latin_ratio > 0.18:
        return None, round(max(0.0, quality - 0.2), 3), "latin_hangul_mix_noise"
    if hangul_chars and cjk_ideographs >= 1:
        return None, round(max(0.0, quality - 0.2), 3), "cjk_mix_noise"
    if len(normalized) >= 8 and weird_ratio > 0.08:
        return None, round(max(0.0, quality - 0.1), 3), "symbol_noise"

    return normalized, round(max(0.0, quality), 3), "usable"


def overlap_ratio(left: dict, right: dict) -> float:
    x1 = max(left["x"], right["x"])
    y1 = max(left["y"], right["y"])
    x2 = min(left["x"] + left["width"], right["x"] + right["width"])
    y2 = min(left["y"] + left["height"], right["y"] + right["height"])
    intersection = max(0, x2 - x1) * max(0, y2 - y1)
    left_area = max(1, left["width"] * left["height"])
    right_area = max(1, right["width"] * right["height"])
    return intersection / float(min(left_area, right_area))


def build_windows_ocr_script() -> str:
    return r"""
param([string]$ImagePath, [string]$LanguageTag)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
[void][Windows.Storage.StorageFile, Windows.Storage, ContentType=WindowsRuntime]
[void][Windows.Storage.FileAccessMode, Windows.Storage, ContentType=WindowsRuntime]
[void][Windows.Storage.Streams.IRandomAccessStream, Windows.Storage.Streams, ContentType=WindowsRuntime]
[void][Windows.Graphics.Imaging.BitmapDecoder, Windows.Foundation, ContentType=WindowsRuntime]
[void][Windows.Graphics.Imaging.SoftwareBitmap, Windows.Foundation, ContentType=WindowsRuntime]
[void][Windows.Graphics.Imaging.BitmapPixelFormat, Windows.Foundation, ContentType=WindowsRuntime]
[void][Windows.Graphics.Imaging.BitmapAlphaMode, Windows.Foundation, ContentType=WindowsRuntime]
[void][Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType=WindowsRuntime]
[void][Windows.Media.Ocr.OcrResult, Windows.Foundation, ContentType=WindowsRuntime]
[void][Windows.Globalization.Language, Windows.Foundation, ContentType=WindowsRuntime]

function Await($op, [Type]$resultType) {
  $method = [System.WindowsRuntimeSystemExtensions].GetMethods() |
    Where-Object { $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 } |
    Select-Object -First 1
  $generic = $method.MakeGenericMethod($resultType)
  $task = $generic.Invoke($null, @($op))
  $task.GetAwaiter().GetResult()
}

$file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($ImagePath)) ([Windows.Storage.StorageFile])
$stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
$decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
$bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
$bitmap = [Windows.Graphics.Imaging.SoftwareBitmap]::Convert(
  $bitmap,
  [Windows.Graphics.Imaging.BitmapPixelFormat]::Bgra8,
  [Windows.Graphics.Imaging.BitmapAlphaMode]::Premultiplied
)

$language = [Windows.Globalization.Language]::new($LanguageTag)
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($language)
if ($null -eq $engine) {
  '[]'
  exit 0
}

$result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
$rows = foreach ($line in $result.Lines) {
  $words = @($line.Words)
  if ($words.Count -eq 0) { continue }

  $xs = @()
  $ys = @()
  $x2s = @()
  $y2s = @()
  foreach ($word in $words) {
    $rect = $word.BoundingRect
    $xs += [int]$rect.X
    $ys += [int]$rect.Y
    $x2s += [int]($rect.X + $rect.Width)
    $y2s += [int]($rect.Y + $rect.Height)
  }

  [PSCustomObject]@{
    text = $line.Text
    x = ($xs | Measure-Object -Minimum).Minimum
    y = ($ys | Measure-Object -Minimum).Minimum
    width = (($x2s | Measure-Object -Maximum).Maximum - ($xs | Measure-Object -Minimum).Minimum)
    height = (($y2s | Measure-Object -Maximum).Maximum - ($ys | Measure-Object -Minimum).Minimum)
  }
}

$rows | ConvertTo-Json -Compress -Depth 4
"""


def score_ocr_candidates(candidates: list[dict]) -> int:
    score = 0
    for candidate in candidates:
        text = candidate.get("text") or ""
        meaningful_chars = re.findall(r"[A-Za-z0-9\u3131-\u318E\uAC00-\uD7A3]", text)
        score += len(meaningful_chars) + 4
    return score


def detect_windows_ocr_candidates(image: Image.Image) -> list[dict]:
    script = build_windows_ocr_script()
    language_tags = ("ko", "en-US")
    best_candidates: list[dict] = []
    best_score = -1

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temp_file:
        temp_path = Path(temp_file.name)
    with tempfile.NamedTemporaryFile(
        suffix=".ps1", delete=False, mode="w", encoding="utf-8"
    ) as script_file:
        script_path = Path(script_file.name)
        script_file.write(script)
    try:
        image.convert("RGBA").save(temp_path, format="PNG")

        for language_tag in language_tags:
            completed = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(script_path),
                    str(temp_path),
                    language_tag,
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            if completed.returncode != 0:
                continue

            payload = completed.stdout.strip() or "[]"
            try:
                raw_rows = json.loads(payload)
            except json.JSONDecodeError:
                continue

            if isinstance(raw_rows, dict):
                raw_rows = [raw_rows]

            candidates = []
            for row in raw_rows:
                text = " ".join(str(row.get("text", "")).split())
                if not is_meaningful_ocr_text(text):
                    continue
                candidates.append(
                    {
                        "x": int(row.get("x", 0)),
                        "y": int(row.get("y", 0)),
                        "width": int(row.get("width", 0)),
                        "height": int(row.get("height", 0)),
                        "confidence": 0.95,
                        "fragment_count": 2,
                        "source": "windows_ocr",
                        "text": text,
                        "raw_text": text,
                        "text_lines": [text],
                        "language": language_tag,
                    }
                )

            score = score_ocr_candidates(candidates)
            if score > best_score:
                best_score = score
                best_candidates = candidates
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        try:
            script_path.unlink(missing_ok=True)
        except OSError:
            pass

    best_candidates.sort(key=lambda item: (item["y"], item["x"]))
    return reindex_candidates(best_candidates)


def reindex_candidates(candidates: list[dict]) -> list[dict]:
    indexed = []
    for index, candidate in enumerate(candidates, start=1):
        indexed.append(
            {
                **candidate,
                "id": f"text-candidate-{index:02d}",
            }
        )
    return indexed


def supplement_with_heuristic_candidates(
    primary_candidates: list[dict],
    heuristic_candidates: list[dict],
) -> list[dict]:
    combined = list(primary_candidates)
    for candidate in heuristic_candidates:
        if any(overlap_ratio(candidate, existing) >= 0.45 for existing in combined):
            continue
        combined.append(candidate)

    combined.sort(key=lambda item: (item["y"], item["x"]))
    return reindex_candidates(combined)


def detect_text_candidates(image: Image.Image, ocr_engine: str) -> list[dict]:
    heuristic_candidates = detect_heuristic_text_candidates(image)
    if ocr_engine == "none":
        return heuristic_candidates

    ocr_candidates = detect_windows_ocr_candidates(image) if ocr_engine in ("auto", "windows") else []
    if not ocr_candidates:
        return heuristic_candidates

    normalized_candidates = []
    for candidate in ocr_candidates:
        corrected_text, was_corrected = apply_ocr_text_corrections(
            candidate.get("text") or ""
        )
        normalized_text, text_quality, text_status = assess_ocr_text_quality(
            corrected_text
        )
        if normalized_text and was_corrected and text_status == "usable":
            text_status = "usable_corrected"
        normalized_candidates.append(
            {
                **candidate,
                "text": normalized_text,
                "text_quality": text_quality,
                "text_status": text_status,
                "correction_applied": was_corrected,
                "source": (
                    "windows_ocr_corrected"
                    if normalized_text and was_corrected
                    else "windows_ocr"
                    if normalized_text
                    else "windows_ocr_box"
                ),
            }
        )

    return supplement_with_heuristic_candidates(
        primary_candidates=normalized_candidates,
        heuristic_candidates=heuristic_candidates,
    )


def build_text_exclusion_mask(
    size: tuple[int, int],
    candidates: list[dict],
    padding: int,
) -> np.ndarray:
    width, height = size
    mask = np.zeros((height, width), dtype=bool)

    for candidate in candidates:
        x1 = max(0, candidate["x"] - padding)
        y1 = max(0, candidate["y"] - padding)
        x2 = min(width, candidate["x"] + candidate["width"] + padding)
        y2 = min(height, candidate["y"] + candidate["height"] + padding)
        mask[y1:y2, x1:x2] = True

    return mask


def odd_kernel_size(value: int) -> int:
    return value if value % 2 == 1 else value + 1


def detect_raster_regions(
    image: Image.Image,
    colors: int,
    blur_radius: float,
    alpha_threshold: int,
    exclusion_mask: np.ndarray | None = None,
) -> tuple[list[dict], np.ndarray]:
    rgba = image.convert("RGBA")
    rgba_array = np.array(rgba)
    white = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
    original_rgb = np.array(Image.alpha_composite(white, rgba).convert("RGB")).astype(
        np.float32
    )
    quantized_rgb, alpha_mask = quantize_image(
        image=image,
        colors=max(8, min(colors, 16)),
        blur_radius=blur_radius,
        alpha_threshold=alpha_threshold,
    )
    quantized_rgb = quantized_rgb.astype(np.float32)

    valid_mask = alpha_mask.copy()
    if exclusion_mask is not None:
        valid_mask &= ~exclusion_mask

    valid_values = valid_mask.sum()
    if valid_values == 0:
        return [], np.zeros((image.height, image.width), dtype=bool)

    error_map = np.abs(original_rgb - quantized_rgb).mean(axis=2)
    threshold = max(14.0, float(np.percentile(error_map[valid_mask], 72)))
    texture_mask = (error_map >= threshold) & valid_mask

    kernel_size = odd_kernel_size(max(5, min(image.size) // 100))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size))
    bitmap = (texture_mask.astype(np.uint8)) * 255
    bitmap = cv2.morphologyEx(bitmap, cv2.MORPH_CLOSE, kernel)
    bitmap = cv2.morphologyEx(bitmap, cv2.MORPH_OPEN, kernel)

    component_count, labels, stats, _ = cv2.connectedComponentsWithStats(bitmap, 8)
    total_pixels = image.width * image.height
    raster_regions = []
    exclusion_bitmap = np.zeros((image.height, image.width), dtype=np.uint8)

    for component_id in range(1, component_count):
        x, y, width, height, area = stats[component_id]
        if area < total_pixels * 0.01:
            continue
        if width < image.width * 0.08 and height < image.height * 0.08:
            continue

        fill_ratio = area / float(max(1, width * height))
        if fill_ratio < 0.18:
            continue

        component_mask = labels == component_id
        mean_error = float(error_map[component_mask].mean())
        if mean_error < threshold and area < total_pixels * 0.03:
            continue

        alpha = (component_mask[y : y + height, x : x + width].astype(np.uint8)) * 255
        alpha = cv2.dilate(alpha, np.ones((3, 3), np.uint8), iterations=1)
        alpha = cv2.medianBlur(alpha, 3)
        crop = rgba_array[y : y + height, x : x + width].copy()
        crop[:, :, 3] = np.minimum(crop[:, :, 3], alpha)
        region_image = Image.fromarray(crop, mode="RGBA")

        exclusion_bitmap[component_mask] = 255
        raster_regions.append(
            {
                "id": f"raster-region-{len(raster_regions) + 1:02d}",
                "x": int(x),
                "y": int(y),
                "width": int(width),
                "height": int(height),
                "image": region_image,
                "mean_error": round(mean_error, 2),
                "fill_ratio": round(fill_ratio, 3),
            }
        )

    raster_regions.sort(
        key=lambda region: region["width"] * region["height"] * region["mean_error"],
        reverse=True,
    )
    raster_regions = raster_regions[:6]

    filtered_bitmap = np.zeros_like(exclusion_bitmap)
    for region in raster_regions:
        x1 = region["x"]
        y1 = region["y"]
        x2 = x1 + region["width"]
        y2 = y1 + region["height"]
        filtered_bitmap[y1:y2, x1:x2] = np.maximum(
            filtered_bitmap[y1:y2, x1:x2],
            np.array(region["image"])[:, :, 3],
        )

    filtered_bitmap = cv2.dilate(filtered_bitmap, np.ones((5, 5), np.uint8), iterations=1)
    return raster_regions, filtered_bitmap > 0


def vectorize_image(
    image: Image.Image,
    colors: int,
    min_area: float,
    simplify: float,
    blur_radius: float,
    alpha_threshold: int,
    merge_distance: float,
    exclusion_mask: np.ndarray | None = None,
) -> dict:
    rgb_array, alpha_mask = quantize_image(
        image=image,
        colors=colors,
        blur_radius=blur_radius,
        alpha_threshold=alpha_threshold,
    )
    rgb_array = merge_similar_colors(
        rgb_array=rgb_array,
        alpha_mask=alpha_mask,
        merge_distance=merge_distance,
    )

    working_mask = alpha_mask.copy()
    if exclusion_mask is not None:
        working_mask &= ~exclusion_mask

    valid_pixels = rgb_array[working_mask]
    if valid_pixels.size == 0:
        return {"width": image.width, "height": image.height, "layers": []}

    unique_colors, counts = np.unique(valid_pixels, axis=0, return_counts=True)
    order = np.argsort(-counts)
    layers = []
    total_pixels = int(valid_pixels.shape[0])

    for order_index in order:
        color = unique_colors[order_index]
        pixel_count = int(counts[order_index])
        mask = np.all(rgb_array == color, axis=2) & working_mask
        if not np.any(mask):
            continue

        bitmap = (mask.astype(np.uint8)) * 255
        contours = cv2.findContours(
            bitmap, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE
        )[-2]

        paths = []
        for contour in contours:
            area = abs(cv2.contourArea(contour))
            if area < min_area:
                continue

            epsilon = max(0.0, simplify)
            if epsilon > 0:
                contour = cv2.approxPolyDP(contour, epsilon=epsilon, closed=True)

            path_data = contour_to_svg_path(contour)
            if path_data:
                paths.append(path_data)

        if not paths:
            continue

        hex_value = color_hex(color)
        role = "background" if not layers and pixel_count / max(1, total_pixels) >= 0.25 else "shape"
        layers.append(
            {
                "id": f"{role}-{path_id(hex_value)}-{len(layers) + 1}",
                "color": hex_value,
                "role": role,
                "pixel_count": pixel_count,
                "paths": paths,
            }
        )

    return {"width": image.width, "height": image.height, "layers": layers}


def svg_header(width: float, height: float) -> list[str]:
    return [
        '<?xml version="1.0" encoding="UTF-8"?>',
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'width="{fmt(width)}" height="{fmt(height)}" '
            f'viewBox="0 0 {fmt(width)} {fmt(height)}">'
        ),
        "  <metadata>Generated by SlideRefine Figma package prototype</metadata>",
    ]


def svg_footer() -> list[str]:
    return ["</svg>"]


def append_vector_layers(lines: list[str], layers: list[dict], indent: str) -> None:
    lines.append(f'{indent}<g id="vector-regions" data-role="vector-regions">')
    for layer in layers:
        lines.append(
            f'{indent}  <g id="{layer["id"]}" data-color="{layer["color"]}" '
            f'data-role="{layer["role"]}" data-pixels="{layer["pixel_count"]}">'
        )
        for index, path_data in enumerate(layer["paths"], start=1):
            lines.append(
                f'{indent}    <path id="{layer["id"]}-shape-{index}" '
                f'fill="{layer["color"]}" fill-rule="evenodd" d="{path_data}"/>'
            )
        lines.append(f"{indent}  </g>")
    lines.append(f"{indent}</g>")


def append_raster_regions(lines: list[str], regions: list[dict], indent: str) -> None:
    if not regions:
        return

    lines.append(f'{indent}<g id="raster-regions" data-role="raster-regions">')
    for region in regions:
        lines.append(
            f'{indent}  <image id="{region["id"]}" data-role="raster-region" '
            f'x="{fmt(region["x"])}" y="{fmt(region["y"])}" '
            f'width="{fmt(region["width"])}" height="{fmt(region["height"])}" '
            f'href="{image_to_data_uri(region["image"])}"/>'
        )
    lines.append(f"{indent}</g>")


def append_text_candidates(
    lines: list[str],
    candidates: list[dict],
    indent: str,
) -> None:
    if not candidates:
        return

    lines.append(f'{indent}<g id="text-candidates" data-role="text-candidates">')
    for candidate in candidates:
        attributes = [
            f'id="{candidate["id"]}"',
            f'x="{fmt(candidate["x"])}"',
            f'y="{fmt(candidate["y"])}"',
            f'width="{fmt(candidate["width"])}"',
            f'height="{fmt(candidate["height"])}"',
            'fill="none"',
            f'stroke="{SVG_GUIDE_STROKE}"',
            'stroke-width="1.5"',
            'stroke-dasharray="6 4"',
            'data-role="text-candidate"',
            f'data-confidence="{fmt(candidate["confidence"])}"',
            f'data-source="{candidate.get("source", "unknown")}"',
        ]
        if candidate.get("text"):
            preview = candidate["text"].replace("\n", " / ")
            attributes.append(f'data-text="{escape_attr(preview)}"')
        if candidate.get("text_status"):
            attributes.append(
                f'data-text-status="{escape_attr(str(candidate["text_status"]))}"'
            )
        lines.append(
            f'{indent}  <rect {" ".join(attributes)}/>'
        )
    lines.append(f"{indent}</g>")


def image_to_data_uri(image: Image.Image) -> str:
    with io.BytesIO() as buffer:
        image.convert("RGBA").save(buffer, format="PNG")
        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def image_to_base64_png(image: Image.Image) -> str:
    with io.BytesIO() as buffer:
        image.convert("RGBA").save(buffer, format="PNG")
        return base64.b64encode(buffer.getvalue()).decode("ascii")


def relative_luminance(color: tuple[int, int, int]) -> float:
    def convert(channel: int) -> float:
        value = channel / 255.0
        return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4

    r, g, b = [convert(channel) for channel in color]
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(left: tuple[int, int, int], right: tuple[int, int, int]) -> float:
    l1 = relative_luminance(left)
    l2 = relative_luminance(right)
    lighter = max(l1, l2)
    darker = min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


def detect_palette(crop: Image.Image, colors: int = 5) -> list[dict]:
    array = np.array(
        crop.convert("RGB")
        .quantize(colors=max(2, colors), method=Image.Quantize.MEDIANCUT)
        .convert("RGB")
    )
    flat = array.reshape(-1, 3)
    unique_colors, counts = np.unique(flat, axis=0, return_counts=True)
    order = np.argsort(-counts)
    palette = []
    for index in order:
        color = tuple(int(channel) for channel in unique_colors[index])
        palette.append(
            {
                "color": color,
                "hex": color_hex(color),
                "count": int(counts[index]),
            }
        )
    return palette


def estimate_background_color(crop: Image.Image) -> tuple[int, int, int]:
    rgb = np.array(crop.convert("RGB"))
    if rgb.size == 0:
        return (255, 255, 255)

    border = max(2, min(rgb.shape[0], rgb.shape[1]) // 12)
    samples = np.concatenate(
        [
            rgb[:border, :, :].reshape(-1, 3),
            rgb[-border:, :, :].reshape(-1, 3),
            rgb[:, :border, :].reshape(-1, 3),
            rgb[:, -border:, :].reshape(-1, 3),
        ],
        axis=0,
    )
    palette_img = Image.fromarray(samples.reshape(1, samples.shape[0], 3), mode="RGB")
    palette = detect_palette(palette_img, colors=4)
    if not palette:
        return (255, 255, 255)
    return palette[0]["color"]


def extract_text_ink_mask(
    crop: Image.Image,
    *,
    threshold_percentile: float = 78.0,
    minimum_threshold: float = 24.0,
) -> tuple[tuple[int, int, int], np.ndarray, np.ndarray]:
    background = estimate_background_color(crop)
    crop_rgb = np.array(crop.convert("RGB"))
    background_arr = np.array(background, dtype=np.float32)
    diff = np.linalg.norm(crop_rgb.astype(np.float32) - background_arr, axis=2)
    if diff.size == 0:
        return background, diff, np.zeros((crop.height, crop.width), dtype=bool)
    threshold = max(minimum_threshold, float(np.percentile(diff, threshold_percentile)))
    ink_mask = diff >= threshold
    return background, diff, ink_mask


def build_text_presence_mask(
    crop: Image.Image,
    *,
    threshold_percentile: float = 64.0,
    minimum_threshold: float = 12.0,
) -> tuple[tuple[int, int, int], np.ndarray]:
    background, diff, ink_mask = extract_text_ink_mask(
        crop,
        threshold_percentile=threshold_percentile,
        minimum_threshold=minimum_threshold,
    )
    rgb = np.array(crop.convert("RGB"))
    if rgb.size == 0:
        return background, np.zeros((crop.height, crop.width), dtype=bool)

    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32)
    background_luma = float(
        background[0] * 0.2126 + background[1] * 0.7152 + background[2] * 0.0722
    )
    contrast_mask = diff >= 9.0
    dark_text = (gray <= background_luma - 15.0) & contrast_mask
    light_text = (gray >= background_luma + 15.0) & contrast_mask
    saturated_text = (hsv[:, :, 1] >= 42) & (diff >= 10.0)
    return background, ink_mask | dark_text | light_text | saturated_text


def detect_palette_from_mask(
    crop_rgb: np.ndarray,
    mask: np.ndarray,
    *,
    colors: int = 4,
) -> list[dict]:
    if not np.any(mask):
        return []
    ink_pixels = crop_rgb[mask].reshape(-1, 3).astype(np.uint8)
    if ink_pixels.size == 0:
        return []
    palette_img = Image.fromarray(
        ink_pixels.reshape(1, ink_pixels.shape[0], 3), mode="RGB"
    )
    return detect_palette(palette_img, colors=colors)


def pick_prominent_text_hex(
    crop: Image.Image,
    background: tuple[int, int, int],
) -> str | None:
    palette = detect_palette(crop, colors=6)
    if not palette:
        return None

    total = sum(item["count"] for item in palette)
    best = None
    best_score = -1.0
    for swatch in palette:
        if swatch["color"] == background:
            continue
        ratio = swatch["count"] / float(max(1, total))
        contrast = contrast_ratio(swatch["color"], background)
        color = swatch["color"]
        saturation = (max(color) - min(color)) / 255.0
        if contrast < 1.2:
            continue
        score = ratio * contrast + saturation * 0.35
        if score > best_score:
            best_score = score
            best = swatch["hex"]
    return best


def infer_segment_boxes_from_words(
    ink_mask: np.ndarray,
    words: list[str],
) -> list[tuple[int, int]]:
    if len(words) != 2 or not np.any(ink_mask):
        return []

    height, width = ink_mask.shape
    column_strength = ink_mask.sum(axis=0).astype(np.float32)
    chars = [max(1, len(word)) for word in words]
    total_chars = sum(chars)
    boundary_guess = int(round(width * (chars[0] / float(total_chars))))
    window = max(12, int(width * 0.12))
    start = max(1, boundary_guess - window)
    end = min(width - 2, boundary_guess + window)
    if end <= start:
        return []

    local = column_strength[start:end]
    boundary = start + int(np.argmin(local))

    left_cols = np.where(np.any(ink_mask[:, :boundary], axis=0))[0]
    right_cols = np.where(np.any(ink_mask[:, boundary:], axis=0))[0]
    if left_cols.size == 0 or right_cols.size == 0:
        return []

    left_box = (int(left_cols.min()), int(left_cols.max()) + 1)
    right_box = (
        int(boundary + right_cols.min()),
        int(boundary + right_cols.max()) + 1,
    )
    if left_box[1] - left_box[0] < width * 0.08 or right_box[1] - right_box[0] < width * 0.08:
        return []
    return [left_box, right_box]


def infer_text_style_from_crop(
    crop: Image.Image,
    text: str,
) -> tuple[dict, list[dict]]:
    width = max(1, crop.width)
    height = max(1, crop.height)
    crop_rgb = np.array(crop.convert("RGB"))
    background, ink_mask = build_text_presence_mask(
        crop,
        threshold_percentile=60.0,
        minimum_threshold=10.0,
    )

    rows = np.where(np.any(ink_mask, axis=1))[0]
    cols = np.where(np.any(ink_mask, axis=0))[0]
    left_margin = int(cols[0]) if cols.size else 0
    right_margin = width - int(cols[-1]) - 1 if cols.size else 0
    centered = cols.size and abs(left_margin - right_margin) <= width * 0.12 and min(
        left_margin, right_margin
    ) >= width * 0.04
    text_align = "CENTER" if centered else "LEFT"

    full_palette = detect_palette(crop, colors=6)
    ink_palette = detect_palette_from_mask(crop_rgb, ink_mask, colors=4)

    significant_colors = []
    total_pixels = max(1, int(ink_mask.sum()))
    for swatch in ink_palette:
        ratio = swatch["count"] / float(total_pixels)
        contrast = contrast_ratio(swatch["color"], background)
        if ratio >= 0.015 and contrast >= 1.45:
            significant_colors.append(
                {
                    **swatch,
                    "ratio": ratio,
                    "contrast": contrast,
                }
            )

    if significant_colors:
        primary = max(
            significant_colors,
            key=lambda swatch: swatch["count"] * swatch["contrast"],
        )
        fill_hex = primary["hex"]
    else:
        fill_hex = "#111111" if relative_luminance(background) > 0.45 else "#ffffff"

    line_count = max(1, text.count("\n") + 1)
    font_size = clamp(
        height * (0.82 if line_count == 1 else 0.72),
        11,
        96,
    )
    ink_ratio = float(ink_mask.mean()) if ink_mask.size else 0.0
    stripped_text = text.strip()
    body_like = font_size <= 22 and (
        stripped_text.startswith("\u2022")
        or stripped_text.startswith("-")
        or len(stripped_text) <= 36
    )
    if font_size >= 34:
        font_style = "Bold"
    elif body_like:
        font_style = "Regular"
    elif font_size >= 22 or ink_ratio >= 0.24:
        font_style = "SemiBold"
    else:
        font_style = "Regular"

    style = {
        "fill": fill_hex,
        "font_size_px": round(font_size, 2),
        "font_style": font_style,
        "text_align": text_align,
        "line_height_px": round(font_size * (1.04 if line_count == 1 else 1.18), 2),
        "background_fill": color_hex(background),
        "ink_ratio": round(ink_ratio, 3),
    }

    words = text.split()
    segments: list[dict] = []
    if (
        len(words) == 2
        and width >= 320
        and height >= 44
        and font_size >= 42
    ):
        segment_ranges = infer_segment_boxes_from_words(ink_mask, words)
        if not segment_ranges:
            guess = int(round(width * (max(1, len(words[0])) / float(max(1, len(words[0]) + len(words[1]))))))
            guess = max(int(width * 0.2), min(int(width * 0.8), guess))
            segment_ranges = [(0, guess), (guess, width)]
        if len(segment_ranges) == 2:
            left_box, right_box = segment_ranges
            minimum_word_gap = int(round(max(22.0, font_size * 0.32)))
            if right_box[0] - left_box[1] < minimum_word_gap:
                shifted_start = min(width - 1, left_box[1] + minimum_word_gap)
                right_box = (shifted_start, right_box[1])
                segment_ranges = [left_box, right_box]
            local_segments = []
            for index, (start_x, end_x) in enumerate(segment_ranges):
                segment_crop = crop.crop((start_x, 0, end_x, height))
                segment_color = pick_prominent_text_hex(segment_crop, background) or style["fill"]
                local_segments.append(
                    {
                        "text": words[index],
                        "x": start_x,
                        "y": 0,
                        "width": end_x - start_x,
                        "height": height,
                        "style": {
                            **style,
                            "fill": segment_color,
                            "text_align": "LEFT",
                        },
                    }
                )

            if len(local_segments) == 2:
                left_color = local_segments[0]["style"]["fill"]
                right_color = local_segments[1]["style"]["fill"]
                if left_color != right_color:
                    segments = local_segments

    return style, segments


def refine_icon_prefixed_text_candidate(candidate: dict) -> dict:
    text = str(candidate.get("text") or "")
    if not re.match(r"^\s*[!！]\s*\d", text):
        return candidate

    refined_text = re.sub(r"^\s*[!！]\s*", "", text).strip()
    refined_text = re.sub(r"^(\d+)\.\s*", r"\1. ", refined_text)
    height = float(candidate.get("height") or 0)
    width = float(candidate.get("width") or 0)
    trim = min(width * 0.28, max(height * 1.35, 44.0))
    if width - trim < 48:
        return {**candidate, "text": refined_text}

    return {
        **candidate,
        "text": refined_text,
        "x": float(candidate["x"]) + trim,
        "width": width - trim,
        "icon_prefix_trim_px": round(trim, 2),
    }


def infer_text_candidate_design(
    reference_image: Image.Image,
    candidate: dict,
) -> dict:
    if not is_editable_text_candidate(candidate):
        return candidate

    candidate = refine_icon_prefixed_text_candidate(candidate)
    x1 = max(0, int(round(candidate["x"])))
    y1 = max(0, int(round(candidate["y"])))
    x2 = min(reference_image.width, int(round(candidate["x"] + candidate["width"])))
    y2 = min(reference_image.height, int(round(candidate["y"] + candidate["height"])))
    if x2 <= x1 or y2 <= y1:
        return candidate

    crop = reference_image.crop((x1, y1, x2, y2))
    style, local_segments = infer_text_style_from_crop(crop, candidate["text"])
    absolute_segments = []
    for segment in local_segments:
        absolute_segments.append(
            {
                "text": segment["text"],
                "x": round(candidate["x"] + segment["x"], 2),
                "y": round(candidate["y"] + segment["y"], 2),
                "width": round(segment["width"], 2),
                "height": round(candidate["height"], 2),
                "style": segment["style"],
            }
        )

    replacement_policy, replacement_reason = text_replacement_policy(candidate)
    return {
        **candidate,
        "text_style": style,
        "text_segments": absolute_segments,
        "replacement_policy": replacement_policy,
        "replacement_reason": replacement_reason,
    }


def infer_text_candidate_designs(
    reference_image: Image.Image,
    candidates: list[dict],
) -> list[dict]:
    return [infer_text_candidate_design(reference_image, candidate) for candidate in candidates]


def build_rendered_slide_svg(
    slide: dict,
    *,
    include_guides: bool = True,
    include_raster_regions: bool = True,
) -> str:
    lines = svg_header(slide["width"], slide["height"])
    lines.append(f'  <g id="{slide["id"]}" data-role="slide">')

    for element in slide["elements"]:
        vector_width = max(1.0, float(element["vector"]["width"]))
        vector_height = max(1.0, float(element["vector"]["height"]))
        scale_x = float(element["width"]) / vector_width
        scale_y = float(element["height"]) / vector_height
        lines.append(
            f'    <g id="{element["name"]}" data-role="source-element" '
            f'transform="translate({fmt(element["x"])} {fmt(element["y"])}) '
            f'scale({fmt(scale_x)} {fmt(scale_y)})">'
        )
        append_vector_layers(lines, element["vector"]["layers"], indent="      ")
        if include_raster_regions:
            append_raster_regions(lines, element["raster_regions"], indent="      ")
        if include_guides:
            append_text_candidates(lines, element["text_candidates"], indent="      ")
        lines.append("    </g>")

    lines.append("  </g>")
    lines.extend(svg_footer())
    return "\n".join(lines) + "\n"


def write_rendered_slide_svg(slide: dict, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(build_rendered_slide_svg(slide), encoding="utf-8")


def build_hybrid_slide_svg(slide: dict) -> str:
    lines = svg_header(slide["width"], slide["height"])
    reference_image = composite_slide_reference(slide)
    lines.append(f'  <g id="{slide["id"]}" data-role="hybrid-slide">')
    lines.append(
        '    <image id="reference-raster" data-role="reference-raster" '
        f'x="0" y="0" width="{fmt(slide["width"])}" height="{fmt(slide["height"])}" '
        f'href="{image_to_data_uri(reference_image)}"/>'
    )
    append_text_candidates(lines, slide["text_candidates"], indent="    ")
    lines.append("  </g>")
    lines.extend(svg_footer())
    return "\n".join(lines) + "\n"


def write_hybrid_slide_svg(slide: dict, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(build_hybrid_slide_svg(slide), encoding="utf-8")


def resolve_part_path(base_part: str, target: str) -> str:
    if target.startswith("/"):
        return target.lstrip("/")
    base_dir = posixpath.dirname(base_part)
    return posixpath.normpath(posixpath.join(base_dir, target))


def read_relationships(archive: zipfile.ZipFile, slide_part: str) -> dict[str, str]:
    rels_part = slide_part.replace("slides/", "slides/_rels/") + ".rels"
    if rels_part not in archive.namelist():
        return {}

    rels_root = ET.fromstring(archive.read(rels_part))
    relationships = {}
    for rel in rels_root.findall(f"{{{PKG_REL_NS}}}Relationship"):
        relationships[rel.attrib["Id"]] = resolve_part_path(
            slide_part, rel.attrib["Target"]
        )
    return relationships


def extract_crop_box(
    image_size: tuple[int, int],
    src_rect: ET.Element | None,
) -> tuple[int, int, int, int]:
    width, height = image_size
    if src_rect is None:
        return (0, 0, width, height)

    left = float(src_rect.attrib.get("l", "0")) / 100000.0
    top = float(src_rect.attrib.get("t", "0")) / 100000.0
    right = float(src_rect.attrib.get("r", "0")) / 100000.0
    bottom = float(src_rect.attrib.get("b", "0")) / 100000.0

    crop_left = int(round(width * left))
    crop_top = int(round(height * top))
    crop_right = int(round(width * (1.0 - right)))
    crop_bottom = int(round(height * (1.0 - bottom)))

    crop_right = max(crop_left + 1, crop_right)
    crop_bottom = max(crop_top + 1, crop_bottom)
    return (crop_left, crop_top, crop_right, crop_bottom)


def load_ppt_pictures(archive: zipfile.ZipFile, slide_part: str) -> list[dict]:
    slide_root = ET.fromstring(archive.read(slide_part))
    relationships = read_relationships(archive, slide_part)
    pictures = []

    for picture_index, pic in enumerate(
        slide_root.findall(".//p:pic", PPT_NS), start=1
    ):
        blip = pic.find(".//a:blip", PPT_NS)
        if blip is None:
            continue

        rel_id = blip.attrib.get(f'{{{PPT_NS["r"]}}}embed')
        image_part = relationships.get(rel_id or "")
        if not image_part or image_part not in archive.namelist():
            continue

        xfrm = pic.find("./p:spPr/a:xfrm", PPT_NS)
        off = xfrm.find("./a:off", PPT_NS) if xfrm is not None else None
        ext = xfrm.find("./a:ext", PPT_NS) if xfrm is not None else None
        if off is None or ext is None:
            continue

        with Image.open(io.BytesIO(archive.read(image_part))) as source_image:
            cropped = source_image.convert("RGBA")
            src_rect = pic.find("./p:blipFill/a:srcRect", PPT_NS)
            crop_box = extract_crop_box(cropped.size, src_rect)
            cropped = cropped.crop(crop_box)
            pictures.append(
                {
                    "name": f"picture-{picture_index}",
                    "x": emu_to_px(int(off.attrib["x"])),
                    "y": emu_to_px(int(off.attrib["y"])),
                    "width": emu_to_px(int(ext.attrib["cx"])),
                    "height": emu_to_px(int(ext.attrib["cy"])),
                    "image": cropped.copy(),
                    "source": image_part,
                }
            )

    return pictures


def read_slide_size(archive: zipfile.ZipFile) -> tuple[float, float]:
    presentation_root = ET.fromstring(archive.read("ppt/presentation.xml"))
    sld_size = presentation_root.find("./p:sldSz", PPT_NS)
    if sld_size is None:
        raise ValueError("Slide size metadata not found in ppt/presentation.xml")
    return (
        emu_to_px(int(sld_size.attrib["cx"])),
        emu_to_px(int(sld_size.attrib["cy"])),
    )


def sorted_slide_parts(names: list[str]) -> list[str]:
    def sort_key(value: str) -> tuple[int, str]:
        match = re.search(r"slide(\d+)\.xml$", value)
        return (int(match.group(1)) if match else 10**9, value)

    return sorted(
        [
            name
            for name in names
            if name.startswith("ppt/slides/") and re.search(r"slide\d+\.xml$", name)
        ],
        key=sort_key,
    )


def load_image_slide(image_path: Path, slide_id: str) -> dict:
    with Image.open(image_path) as image:
        rgba = image.convert("RGBA")
        return {
            "id": slide_id,
            "name": image_path.stem,
            "width": float(rgba.width),
            "height": float(rgba.height),
            "elements": [
                {
                    "name": "canvas",
                    "x": 0.0,
                    "y": 0.0,
                    "width": float(rgba.width),
                    "height": float(rgba.height),
                    "image": rgba.copy(),
                    "source": str(image_path),
                }
            ],
        }


def load_directory_slides(directory: Path) -> list[dict]:
    image_paths = sorted(
        [
            child
            for child in directory.iterdir()
            if child.is_file() and child.suffix.lower() in IMAGE_EXTENSIONS
        ]
    )
    if not image_paths:
        raise ValueError(f"No supported image files were found in {directory}")

    return [
        load_image_slide(image_path=image_path, slide_id=f"slide-{index:02d}")
        for index, image_path in enumerate(image_paths, start=1)
    ]


def load_pptx_slides(source_path: Path) -> list[dict]:
    slides = []
    with zipfile.ZipFile(source_path) as archive:
        slide_width, slide_height = read_slide_size(archive)
        slide_parts = sorted_slide_parts(archive.namelist())
        if not slide_parts:
            raise ValueError("No slide XML parts were found in the PPTX file.")

        for slide_number, slide_part in enumerate(slide_parts, start=1):
            slides.append(
                {
                    "id": f"slide-{slide_number:02d}",
                    "name": f"{source_path.stem}-slide-{slide_number:02d}",
                    "width": slide_width,
                    "height": slide_height,
                    "elements": load_ppt_pictures(archive, slide_part),
                }
            )
    return slides


def load_source_slides(source_path: Path) -> list[dict]:
    if source_path.is_dir():
        return load_directory_slides(source_path)

    suffix = source_path.suffix.lower()
    if suffix == ".pptx":
        return load_pptx_slides(source_path)
    if suffix in IMAGE_EXTENSIONS:
        return [load_image_slide(source_path, slide_id="slide-01")]

    raise ValueError(
        "Unsupported input type. Use an image file, a directory of slide images, or a .pptx deck."
    )


def scale_candidate_to_slide(candidate: dict, element: dict) -> dict:
    image_width = max(1.0, float(element["image"].width))
    image_height = max(1.0, float(element["image"].height))
    scale_x = float(element["width"]) / image_width
    scale_y = float(element["height"]) / image_height
    return {
        "id": candidate["id"],
        "source_element": element["name"],
        "x": round(float(element["x"]) + candidate["x"] * scale_x, 2),
        "y": round(float(element["y"]) + candidate["y"] * scale_y, 2),
        "width": round(candidate["width"] * scale_x, 2),
        "height": round(candidate["height"] * scale_y, 2),
        "confidence": candidate["confidence"],
        "source": candidate.get("source", "unknown"),
        "text": candidate.get("text"),
        "raw_text": candidate.get("raw_text"),
        "language": candidate.get("language"),
        "text_quality": candidate.get("text_quality"),
        "text_status": candidate.get("text_status"),
        "correction_applied": candidate.get("correction_applied"),
    }


def scale_raster_region_to_slide(region: dict, element: dict) -> dict:
    image_width = max(1.0, float(element["image"].width))
    image_height = max(1.0, float(element["image"].height))
    scale_x = float(element["width"]) / image_width
    scale_y = float(element["height"]) / image_height
    return {
        "id": region["id"],
        "source_element": element["name"],
        "x": round(float(element["x"]) + region["x"] * scale_x, 2),
        "y": round(float(element["y"]) + region["y"] * scale_y, 2),
        "width": round(region["width"] * scale_x, 2),
        "height": round(region["height"] * scale_y, 2),
        "image_base64": image_to_base64_png(region["image"]),
        "mean_error": region.get("mean_error"),
        "fill_ratio": region.get("fill_ratio"),
    }


def render_element(element: dict, options: argparse.Namespace) -> dict:
    text_candidates = []
    text_exclusion_mask = None
    raster_exclusion_mask = None
    raster_regions = []

    if not options.skip_text_detection:
        text_candidates = detect_text_candidates(
            image=element["image"],
            ocr_engine=options.ocr_engine,
        )
        if options.text_handling == "exclude" and text_candidates:
            text_exclusion_mask = build_text_exclusion_mask(
                size=element["image"].size,
                candidates=text_candidates,
                padding=max(0, options.text_padding),
            )

    raster_regions, raster_exclusion_mask = detect_raster_regions(
        image=element["image"],
        colors=options.colors,
        blur_radius=options.blur,
        alpha_threshold=options.alpha_threshold,
        exclusion_mask=text_exclusion_mask,
    )

    exclusion_mask = None
    if text_exclusion_mask is not None:
        exclusion_mask = text_exclusion_mask.copy()
    if raster_exclusion_mask is not None:
        exclusion_mask = (
            raster_exclusion_mask.copy()
            if exclusion_mask is None
            else (exclusion_mask | raster_exclusion_mask)
        )

    vector = vectorize_image(
        image=element["image"],
        colors=options.colors,
        min_area=options.min_area,
        simplify=options.simplify,
        blur_radius=options.blur,
        alpha_threshold=options.alpha_threshold,
        merge_distance=options.merge_distance,
        exclusion_mask=exclusion_mask,
    )

    absolute_candidates = [
        scale_candidate_to_slide(candidate, element) for candidate in text_candidates
    ]
    absolute_raster_regions = [
        scale_raster_region_to_slide(region, element) for region in raster_regions
    ]

    return {
        **element,
        "vector": vector,
        "text_candidates": text_candidates,
        "absolute_text_candidates": absolute_candidates,
        "raster_regions": raster_regions,
        "absolute_raster_regions": absolute_raster_regions,
    }


def analyze_slide(reference: Image.Image, slide: dict) -> dict:
    rgb = np.array(reference.convert("RGB"))
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    edges = cv2.Canny(gray, 100, 200)
    edge_density = float((edges > 0).mean())
    sampled = rgb[::4, ::4].reshape(-1, 3)
    sampled_unique_colors = int(len(np.unique(sampled, axis=0)))
    recognized_text_blocks = sum(
        1 for candidate in slide["text_candidates"] if candidate.get("text")
    )
    path_density = slide["vector_path_count"] / max(
        1.0, (slide["width"] * slide["height"]) / 100000.0
    )
    photo_like_complexity = sampled_unique_colors >= 18000 and slide["vector_path_count"] >= 900
    dense_photo_like_complexity = sampled_unique_colors >= 30000 and path_density >= 100

    if dense_photo_like_complexity or slide["vector_path_count"] >= 2200 or (
        path_density >= 180 and recognized_text_blocks < 3
    ):
        profile = "hybrid_review"
        recommendation = (
            "Use the reference PNG as a locked base in Figma and rebuild only the key "
            "text and shape layers from the SVG."
        )
    elif photo_like_complexity:
        profile = "hybrid_review"
        recommendation = (
            "This slide contains photo-like regions. Use the hybrid asset in Figma to "
            "preserve the original design, then edit text and simple shapes on top."
        )
    elif slide["vector_path_count"] >= 1200:
        profile = "vector_with_review"
        recommendation = (
            "Import the SVG first, then compare against the reference PNG for moderate "
            "cleanup and text reconstruction."
        )
    else:
        profile = "vector_first"
        recommendation = (
            "Import the SVG directly in Figma and rebuild text from OCR-backed blocks."
        )

    return {
        "profile": profile,
        "recommended_workflow": recommendation,
        "metrics": {
            "edge_density": round(edge_density, 4),
            "sampled_unique_colors": sampled_unique_colors,
            "path_density_per_100k_px": round(path_density, 2),
            "recognized_text_blocks": recognized_text_blocks,
            "photo_like_complexity": photo_like_complexity,
        },
    }


def render_slide(slide: dict, options: argparse.Namespace) -> dict:
    rendered_elements = [render_element(element, options) for element in slide["elements"]]
    slide_candidates = []
    slide_raster_regions = []
    vector_layer_count = 0
    vector_path_count = 0

    for element in rendered_elements:
        slide_candidates.extend(element["absolute_text_candidates"])
        slide_raster_regions.extend(element["absolute_raster_regions"])
        vector_layer_count += len(element["vector"]["layers"])
        vector_path_count += sum(
            len(layer["paths"]) for layer in element["vector"]["layers"]
        )

    rendered_slide = {
        **slide,
        "elements": rendered_elements,
        "text_candidates": slide_candidates,
        "raster_regions": slide_raster_regions,
        "vector_layer_count": vector_layer_count,
        "vector_path_count": vector_path_count,
    }
    reference_image = composite_slide_reference(rendered_slide)
    rendered_slide["text_candidates"] = infer_text_candidate_designs(
        reference_image=reference_image,
        candidates=rendered_slide["text_candidates"],
    )
    rendered_slide["analysis"] = analyze_slide(
        reference=reference_image,
        slide=rendered_slide,
    )
    return rendered_slide


def composite_slide_reference(slide: dict) -> Image.Image:
    width = max(1, int(round(slide["width"])))
    height = max(1, int(round(slide["height"])))
    canvas = Image.new("RGBA", (width, height), (255, 255, 255, 255))

    for element in slide["elements"]:
        target_width = max(1, int(round(element["width"])))
        target_height = max(1, int(round(element["height"])))
        resized = element["image"].convert("RGBA").resize(
            (target_width, target_height), Image.Resampling.LANCZOS
        )
        canvas.alpha_composite(
            resized, (int(round(element["x"])), int(round(element["y"])))
        )

    return canvas


def build_text_removal_mask(crop: Image.Image, candidate: dict) -> np.ndarray:
    background, ink_mask = build_text_presence_mask(
        crop,
        threshold_percentile=70.0,
        minimum_threshold=14.0,
    )
    rgb = np.array(crop.convert("RGB"))
    if rgb.size == 0:
        return np.zeros((crop.height, crop.width), dtype=np.uint8)

    background_arr = np.array(background, dtype=np.float32)
    diff = np.linalg.norm(rgb.astype(np.float32) - background_arr, axis=2)
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32)
    background_luma = float(
        background[0] * 0.2126 + background[1] * 0.7152 + background[2] * 0.0722
    )
    contrast_mask = diff >= 10.0
    dark_text = (gray <= background_luma - 17.0) & contrast_mask
    light_text = (gray >= background_luma + 17.0) & contrast_mask
    saturated_text = (hsv[:, :, 1] >= 44) & (diff >= 12.0)
    local = (ink_mask | dark_text | light_text | saturated_text).astype(np.uint8) * 255

    coverage = float((local > 0).mean()) if local.size else 0.0
    if coverage <= 0.002:
        local = (ink_mask.astype(np.uint8)) * 255
    elif coverage >= 0.72:
        # A wrong background estimate can mark a whole small crop. Fall back to
        # the stricter ink mask so panel fills and separator lines survive.
        stricter = (ink_mask.astype(np.uint8)) * 255
        if np.any(stricter):
            local = stricter

    text_height = max(1.0, float(candidate.get("height") or crop.height))
    radius = int(round(clamp(text_height / 18.0, 1.0, 7.0)))
    kernel_size = radius * 2 + 1
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size))
    local = cv2.morphologyEx(local, cv2.MORPH_CLOSE, kernel, iterations=1)
    local = cv2.dilate(local, kernel, iterations=1)
    return local


def build_non_text_raster_image(
    slide: dict,
    padding: int = 4,
    feather: float = 1.6,
) -> Image.Image:
    image = composite_slide_reference(slide).convert("RGBA")
    rgba = np.array(image)
    clear_mask = np.zeros((image.height, image.width), dtype=np.uint8)

    for candidate in slide["text_candidates"]:
        if not should_remove_source_text(candidate):
            continue
        candidate_width = float(candidate.get("width") or 0)
        candidate_height = float(candidate.get("height") or 0)
        if candidate_height <= 28:
            pad_x = max(float(padding), min(44.0, max(16.0, candidate_width * 0.35)))
            pad_y = max(float(padding), 8.0)
        else:
            pad_x = max(float(padding), min(18.0, max(6.0, candidate_height * 0.24)))
            pad_y = max(float(padding), min(14.0, max(4.0, candidate_height * 0.18)))
        if candidate_height >= 50:
            pad_x = max(pad_x, 12.0)
            pad_y = max(pad_y, 10.0)
        x1 = max(0, int(round(candidate["x"] - pad_x)))
        y1 = max(0, int(round(candidate["y"] - pad_y)))
        x2 = min(image.width, int(round(candidate["x"] + candidate["width"] + pad_x)))
        y2 = min(image.height, int(round(candidate["y"] + candidate["height"] + pad_y)))
        if x2 <= x1 or y2 <= y1:
            continue
        crop = image.crop((x1, y1, x2, y2))
        local = build_text_removal_mask(crop, candidate)
        if feather > 0:
            local = cv2.GaussianBlur(local, (0, 0), sigmaX=feather, sigmaY=feather)
        clear_mask[y1:y2, x1:x2] = np.maximum(clear_mask[y1:y2, x1:x2], local)

    if np.any(clear_mask):
        rgb = cv2.cvtColor(rgba[:, :, :3], cv2.COLOR_RGB2BGR)
        binary_mask = (clear_mask >= 24).astype(np.uint8) * 255
        binary_mask = cv2.dilate(binary_mask, np.ones((3, 3), np.uint8), iterations=1)
        inpainted = cv2.inpaint(rgb, binary_mask, 3, cv2.INPAINT_TELEA)
        rgba[:, :, :3] = cv2.cvtColor(inpainted, cv2.COLOR_BGR2RGB)
        rgba[:, :, 3] = 255

    return Image.fromarray(rgba, mode="RGBA")


def rect_from_candidate(candidate: dict, padding: float = 0.0) -> dict:
    return {
        "x": float(candidate["x"]) - padding,
        "y": float(candidate["y"]) - padding,
        "width": float(candidate["width"]) + padding * 2,
        "height": float(candidate["height"]) + padding * 2,
    }


def rect_bounds(rect: dict) -> tuple[float, float, float, float]:
    return (
        float(rect["x"]),
        float(rect["y"]),
        float(rect["x"]) + float(rect["width"]),
        float(rect["y"]) + float(rect["height"]),
    )


def rects_intersect(left: dict, right: dict) -> bool:
    left_x1, left_y1, left_x2, left_y2 = rect_bounds(left)
    right_x1, right_y1, right_x2, right_y2 = rect_bounds(right)
    return left_x1 < right_x2 and right_x1 < left_x2 and left_y1 < right_y2 and right_y1 < left_y2


def rect_overlap_ratio(left: dict, right: dict) -> float:
    left_x1, left_y1, left_x2, left_y2 = rect_bounds(left)
    right_x1, right_y1, right_x2, right_y2 = rect_bounds(right)
    intersection = max(0.0, min(left_x2, right_x2) - max(left_x1, right_x1)) * max(
        0.0, min(left_y2, right_y2) - max(left_y1, right_y1)
    )
    left_area = max(1.0, (left_x2 - left_x1) * (left_y2 - left_y1))
    right_area = max(1.0, (right_x2 - right_x1) * (right_y2 - right_y1))
    return intersection / min(left_area, right_area)


def clamp_rect_to_image(rect: dict, width: int, height: int) -> dict:
    x1, y1, x2, y2 = rect_bounds(rect)
    x1 = clamp(x1, 0, width - 1)
    y1 = clamp(y1, 0, height - 1)
    x2 = clamp(x2, x1 + 1, width)
    y2 = clamp(y2, y1 + 1, height)
    return {
        "x": int(round(x1)),
        "y": int(round(y1)),
        "width": int(round(x2 - x1)),
        "height": int(round(y2 - y1)),
    }


def make_panel_region_image(source: Image.Image, rect: dict) -> Image.Image:
    x = int(rect["x"])
    y = int(rect["y"])
    width = int(rect["width"])
    height = int(rect["height"])
    crop = source.crop((x, y, x + width, y + height)).convert("RGBA")
    rgba = np.array(crop)
    # Panel/card regions are intentionally rectangular UI objects. Keeping the
    # crop opaque avoids semi-transparent halos when the group is moved in Figma.
    rgba[:, :, 3] = 255
    return Image.fromarray(rgba, mode="RGBA")


def expanded_photo_region_rect(region: dict, slide: dict) -> dict:
    width = int(round(slide["width"]))
    height = int(round(slide["height"]))
    region_width = float(region["width"])
    region_height = float(region["height"])
    left_pad = max(42.0, region_width * 0.10)
    right_pad = max(70.0, region_width * 0.13)
    top_pad = max(34.0, region_height * 0.07)
    bottom_pad = max(28.0, region_height * 0.06)
    return clamp_rect_to_image(
        {
            "x": float(region["x"]) - left_pad,
            "y": float(region["y"]) - top_pad,
            "width": region_width + left_pad + right_pad,
            "height": region_height + top_pad + bottom_pad,
        },
        width=width,
        height=height,
    )


def make_photo_region_image(reference_image: Image.Image, rect: dict) -> Image.Image:
    x = int(rect["x"])
    y = int(rect["y"])
    width = int(rect["width"])
    height = int(rect["height"])
    crop = reference_image.crop((x, y, x + width, y + height)).convert("RGBA")
    rgba = np.array(crop)
    rgb = rgba[:, :, :3]
    background = np.array(estimate_background_color(crop), dtype=np.float32)
    diff = np.linalg.norm(rgb.astype(np.float32) - background, axis=2)
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    saturation = hsv[:, :, 1]
    value = hsv[:, :, 2]

    foreground = (
        (diff >= 22.0)
        | ((saturation >= 34) & (value >= 65))
        | ((value <= 160) & (diff >= 12.0))
    )
    foreground = foreground.astype(np.uint8) * 255
    kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE,
        (odd_kernel_size(max(5, min(width, height) // 55)),) * 2,
    )
    foreground = cv2.morphologyEx(foreground, cv2.MORPH_CLOSE, kernel, iterations=1)
    foreground = cv2.dilate(foreground, np.ones((3, 3), np.uint8), iterations=1)

    labels_count, labels, stats, _ = cv2.connectedComponentsWithStats(
        (foreground > 0).astype(np.uint8),
        connectivity=8,
    )
    kept = np.zeros_like(foreground)
    min_area = max(80.0, width * height * 0.001)
    for label in range(1, labels_count):
        if stats[label, cv2.CC_STAT_AREA] >= min_area:
            kept[labels == label] = 255
    kept = cv2.morphologyEx(kept, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8), iterations=1)
    kept = cv2.medianBlur(kept, 3)
    rgba[:, :, 3] = np.minimum(rgba[:, :, 3], kept)
    return Image.fromarray(rgba, mode="RGBA")


def remove_overlapping_region_alpha(
    region_image: Image.Image,
    region_rect: dict,
    exclusion_rects: list[dict],
    padding: int = 2,
) -> Image.Image:
    if not exclusion_rects:
        return region_image

    rgba = np.array(region_image.convert("RGBA"))
    region_x1, region_y1, region_x2, region_y2 = rect_bounds(region_rect)
    for exclusion in exclusion_rects:
        x1, y1, x2, y2 = rect_bounds(exclusion)
        overlap_x1 = max(region_x1, x1) - region_x1
        overlap_y1 = max(region_y1, y1) - region_y1
        overlap_x2 = min(region_x2, x2) - region_x1
        overlap_y2 = min(region_y2, y2) - region_y1
        if overlap_x2 <= overlap_x1 or overlap_y2 <= overlap_y1:
            continue
        local_x1 = max(0, int(round(overlap_x1)) - padding)
        local_y1 = max(0, int(round(overlap_y1)) - padding)
        local_x2 = min(rgba.shape[1], int(round(overlap_x2)) + padding)
        local_y2 = min(rgba.shape[0], int(round(overlap_y2)) + padding)
        rgba[local_y1:local_y2, local_x1:local_x2, 3] = 0
    return Image.fromarray(rgba, mode="RGBA")


def expanded_text_anchor(candidate: dict, slide_width: float, slide_height: float) -> dict:
    x_pad = max(62.0, slide_width * 0.045)
    y_pad = 24.0
    if candidate["x"] > slide_width * 0.65:
        x_pad = max(x_pad, 92.0)
    if candidate["y"] > slide_height * 0.78:
        x_pad = max(x_pad, 135.0)
        y_pad = 34.0
    return rect_from_candidate(candidate, padding=0.0) | {
        "x": float(candidate["x"]) - x_pad,
        "y": float(candidate["y"]) - y_pad,
        "width": float(candidate["width"]) + x_pad * 2,
        "height": float(candidate["height"]) + y_pad * 2,
    }


def cluster_text_candidates_for_visual_regions(slide: dict) -> list[list[dict]]:
    slide_width = float(slide["width"])
    slide_height = float(slide["height"])
    candidates = [
        candidate
        for candidate in slide["text_candidates"]
        if is_editable_text_candidate(candidate) and candidate["y"] >= slide_height * 0.24
    ]
    if not candidates:
        return []

    anchors = [
        expanded_text_anchor(candidate, slide_width=slide_width, slide_height=slide_height)
        for candidate in candidates
    ]
    parents = list(range(len(candidates)))

    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parents[right_root] = left_root

    for left in range(len(candidates)):
        for right in range(left + 1, len(candidates)):
            if rects_intersect(anchors[left], anchors[right]):
                union(left, right)

    groups: dict[int, list[dict]] = {}
    for index, candidate in enumerate(candidates):
        groups.setdefault(find(index), []).append(candidate)

    return [
        sorted(group, key=lambda item: (item["y"], item["x"]))
        for group in groups.values()
        if len(group) >= 2
    ]


def text_group_region_rect(group: list[dict], slide: dict) -> dict:
    slide_width = int(round(slide["width"]))
    slide_height = int(round(slide["height"]))
    x1 = min(float(candidate["x"]) for candidate in group)
    y1 = min(float(candidate["y"]) for candidate in group)
    x2 = max(float(candidate["x"] + candidate["width"]) for candidate in group)
    y2 = max(float(candidate["y"] + candidate["height"]) for candidate in group)

    left_pad = max(76.0, slide_width * 0.052)
    right_pad = max(88.0, slide_width * 0.06)
    top_pad = 36.0
    bottom_pad = 34.0
    center_x = (x1 + x2) / 2.0
    if center_x < slide_width * 0.42:
        left_pad = max(left_pad, 140.0)
        if x2 > slide_width * 0.29:
            right_pad = 48.0
        else:
            right_pad = max(right_pad, 98.0)
    if center_x > slide_width * 0.65:
        if x1 < slide_width * 0.74:
            left_pad = max(left_pad, 72.0)
        else:
            left_pad = max(left_pad, 128.0)
        right_pad = max(right_pad, 112.0)
        top_pad = max(top_pad, 44.0)
    if y1 > slide_height * 0.78:
        left_pad = max(left_pad, 165.0)
        right_pad = max(right_pad, 185.0)
        top_pad = 50.0
        bottom_pad = 56.0

    return clamp_rect_to_image(
        {
            "x": x1 - left_pad,
            "y": y1 - top_pad,
            "width": (x2 - x1) + left_pad + right_pad,
            "height": (y2 - y1) + top_pad + bottom_pad,
        },
        width=slide_width,
        height=slide_height,
    )


def build_movable_visual_regions(slide: dict, non_text_raster: Image.Image) -> list[dict]:
    visual_regions: list[dict] = []
    text_region_rects: list[dict] = []
    reference_image = composite_slide_reference(slide)
    for group in cluster_text_candidates_for_visual_regions(slide):
        rect = text_group_region_rect(group, slide)
        text_region_rects.append(rect)
        visual_regions.append(
            {
                "id": f"visual-region-{len(visual_regions) + 1:02d}",
                "kind": "text_panel" if rect["y"] < slide["height"] * 0.78 else "summary_panel",
                "x": rect["x"],
                "y": rect["y"],
                "width": rect["width"],
                "height": rect["height"],
                "text_candidate_ids": [candidate["id"] for candidate in group],
                "image": make_panel_region_image(non_text_raster, rect),
            }
        )

    for raster_region in slide["raster_regions"]:
        rect = expanded_photo_region_rect(raster_region, slide)
        if any(rect_overlap_ratio(rect, text_rect) >= 0.35 for text_rect in text_region_rects):
            continue
        region_image = make_photo_region_image(reference_image, rect)
        region_image = remove_overlapping_region_alpha(
            region_image=region_image,
            region_rect=rect,
            exclusion_rects=text_region_rects,
        )
        visual_regions.append(
            {
                "id": f"visual-region-{len(visual_regions) + 1:02d}",
                "kind": "photo_region",
                "x": rect["x"],
                "y": rect["y"],
                "width": rect["width"],
                "height": rect["height"],
                "text_candidate_ids": [],
                "image": region_image,
                "mean_error": raster_region.get("mean_error"),
                "fill_ratio": raster_region.get("fill_ratio"),
            }
        )

    for region in visual_regions:
        if "image_base64" not in region and region.get("image") is not None:
            region["image_base64"] = image_to_base64_png(region["image"])
    return visual_regions


def build_photo_artifact_mask(crop: Image.Image, alpha_mask: np.ndarray) -> np.ndarray:
    rgb = np.array(crop.convert("RGB"))
    if rgb.size == 0:
        return alpha_mask

    background = np.array(estimate_background_color(crop), dtype=np.float32)
    diff = np.linalg.norm(rgb.astype(np.float32) - background, axis=2)
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    saturation = hsv[:, :, 1]
    value = hsv[:, :, 2]
    artifact = (
        ((diff >= 16.0) & (saturation >= 22) & (value >= 38))
        | ((diff >= 24.0) & (value <= 230))
        | ((saturation >= 42) & (value >= 52) & (value <= 248))
    )
    artifact_mask = artifact.astype(np.uint8) * 255
    artifact_mask = cv2.morphologyEx(
        artifact_mask,
        cv2.MORPH_CLOSE,
        np.ones((3, 3), np.uint8),
        iterations=1,
    )
    combined = np.maximum(alpha_mask, artifact_mask)
    combined = cv2.dilate(combined, np.ones((7, 7), np.uint8), iterations=1)
    return combined


def build_visual_region_removal_mask(
    image: Image.Image,
    visual_regions: list[dict],
) -> np.ndarray:
    width, height = image.size
    mask = np.zeros((height, width), dtype=np.uint8)

    for region in visual_regions:
        x = max(0, int(round(region["x"])))
        y = max(0, int(round(region["y"])))
        region_width = min(width - x, int(round(region["width"])))
        region_height = min(height - y, int(round(region["height"])))
        if region_width <= 0 or region_height <= 0:
            continue

        if region.get("kind") == "photo_region":
            region_image = region.get("image")
            if region_image is not None:
                alpha = np.array(region_image.convert("RGBA"))[:, :, 3]
                alpha = cv2.resize(
                    alpha,
                    (region_width, region_height),
                    interpolation=cv2.INTER_NEAREST,
                )
                local_mask = (alpha >= 8).astype(np.uint8) * 255
                crop = image.crop((x, y, x + region_width, y + region_height))
                local_mask = build_photo_artifact_mask(crop, local_mask)
                mask[y : y + region_height, x : x + region_width] = np.maximum(
                    mask[y : y + region_height, x : x + region_width],
                    local_mask,
                )
            else:
                pad = max(8, min(region_width, region_height) // 35)
                x1 = max(0, x - pad)
                y1 = max(0, y - pad)
                x2 = min(width, x + region_width + pad)
                y2 = min(height, y + region_height + pad)
                mask[y1:y2, x1:x2] = 255
            continue

        if region.get("kind") in {"text_panel", "summary_panel"}:
            x_pad = max(12, min(region_width, region_height) // 24)
            y_pad = max(10, min(region_width, region_height) // 30)
            x1 = max(0, x - x_pad)
            y1 = max(0, y - y_pad)
            x2 = min(width, x + region_width + x_pad)
            y2 = min(height, y + region_height + y_pad)
            mask[y1:y2, x1:x2] = 255
            continue

        region_image = region.get("image")
        if region_image is not None:
            alpha = np.array(region_image.convert("RGBA"))[:, :, 3]
            alpha = cv2.resize(
                alpha,
                (region_width, region_height),
                interpolation=cv2.INTER_LINEAR,
            )
            local_mask = (alpha >= 8).astype(np.uint8) * 255
        else:
            local_mask = np.full((region_height, region_width), 255, dtype=np.uint8)

        kernel = np.ones((5, 5), np.uint8)
        local_mask = cv2.morphologyEx(local_mask, cv2.MORPH_CLOSE, kernel, iterations=1)
        local_mask = cv2.dilate(local_mask, kernel, iterations=1)
        mask[y : y + region_height, x : x + region_width] = np.maximum(
            mask[y : y + region_height, x : x + region_width],
            local_mask,
        )

    return mask


def low_frequency_background_fill(image: Image.Image, mask: np.ndarray) -> np.ndarray:
    rgb = np.array(image.convert("RGB"))
    if not np.any(mask):
        return rgb

    height, width = mask.shape
    max_dimension = max(width, height)
    scale = min(1.0, 360.0 / float(max_dimension))
    if scale < 1.0:
        small_size = (max(1, int(round(width * scale))), max(1, int(round(height * scale))))
        small_rgb = cv2.resize(rgb, small_size, interpolation=cv2.INTER_AREA)
        small_mask = cv2.resize(mask, small_size, interpolation=cv2.INTER_NEAREST)
    else:
        small_size = (width, height)
        small_rgb = rgb.copy()
        small_mask = mask.copy()

    kernel_size = odd_kernel_size(max(5, min(small_size) // 42))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size))
    small_mask = cv2.dilate(small_mask, kernel, iterations=1)
    small_bgr = cv2.cvtColor(small_rgb, cv2.COLOR_RGB2BGR)
    inpainted_small = cv2.inpaint(small_bgr, small_mask, 7, cv2.INPAINT_TELEA)
    inpainted_rgb = cv2.cvtColor(inpainted_small, cv2.COLOR_BGR2RGB)

    if scale < 1.0:
        fill = cv2.resize(inpainted_rgb, (width, height), interpolation=cv2.INTER_CUBIC)
    else:
        fill = inpainted_rgb

    sigma = max(3.5, min(width, height) / 150.0)
    fill = cv2.GaussianBlur(fill, (0, 0), sigmaX=sigma, sigmaY=sigma)
    return fill


def estimate_paper_background_color(image: Image.Image, mask: np.ndarray) -> np.ndarray:
    rgb = np.array(image.convert("RGB"))
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    saturation = hsv[:, :, 1]
    value = hsv[:, :, 2]
    candidates = (mask == 0) & (saturation <= 42) & (value >= 185)
    if int(candidates.sum()) < 500:
        candidates = (mask == 0) & (value >= 175)
    if int(candidates.sum()) < 500:
        return np.array(estimate_background_color(image), dtype=np.float32)
    return np.median(rgb[candidates].reshape(-1, 3), axis=0).astype(np.float32)


def smooth_paper_background_fill(
    image: Image.Image,
    mask: np.ndarray,
    paper_color: np.ndarray,
) -> np.ndarray:
    rgb = np.array(image.convert("RGB"))
    if not np.any(mask):
        return rgb

    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    saturation = hsv[:, :, 1]
    value = hsv[:, :, 2]
    known_paper = (mask == 0) & (saturation <= 58) & (value >= 162)
    if int(known_paper.sum()) < 500:
        return np.full_like(rgb, np.clip(paper_color, 0, 255).astype(np.uint8))

    seed = rgb.copy()
    seed[~known_paper] = np.clip(paper_color, 0, 255).astype(np.uint8)
    unknown = (~known_paper).astype(np.uint8) * 255
    height, width = mask.shape
    max_dimension = max(width, height)
    scale = min(1.0, 420.0 / float(max_dimension))
    if scale < 1.0:
        small_size = (max(1, int(round(width * scale))), max(1, int(round(height * scale))))
        seed_small = cv2.resize(seed, small_size, interpolation=cv2.INTER_AREA)
        unknown_small = cv2.resize(unknown, small_size, interpolation=cv2.INTER_NEAREST)
    else:
        small_size = (width, height)
        seed_small = seed
        unknown_small = unknown

    repaired_small = cv2.inpaint(
        cv2.cvtColor(seed_small, cv2.COLOR_RGB2BGR),
        unknown_small,
        9,
        cv2.INPAINT_TELEA,
    )
    repaired = cv2.cvtColor(repaired_small, cv2.COLOR_BGR2RGB)
    if scale < 1.0:
        repaired = cv2.resize(repaired, (width, height), interpolation=cv2.INTER_CUBIC)

    sigma = max(12.0, min(width, height) / 44.0)
    return cv2.GaussianBlur(repaired, (0, 0), sigmaX=sigma, sigmaY=sigma)


def tone_correct_background_fill(
    fill: np.ndarray,
    mask: np.ndarray,
    paper_color: np.ndarray,
) -> np.ndarray:
    corrected = fill.astype(np.float32).copy()
    if not np.any(mask):
        return corrected.astype(np.uint8)

    hsv = cv2.cvtColor(fill.astype(np.uint8), cv2.COLOR_RGB2HSV)
    saturation = hsv[:, :, 1].astype(np.float32)
    value = hsv[:, :, 2].astype(np.float32)
    paper_luma = float(np.dot(paper_color, np.array([0.2126, 0.7152, 0.0722])))
    fill_luma = (
        fill[:, :, 0].astype(np.float32) * 0.2126
        + fill[:, :, 1].astype(np.float32) * 0.7152
        + fill[:, :, 2].astype(np.float32) * 0.0722
    )

    inside = mask > 0
    red_or_brown_artifact = (
        inside
        & (saturation > 38)
        & (fill[:, :, 0].astype(np.int16) > fill[:, :, 2].astype(np.int16) + 12)
    )
    overly_bright_patch = inside & (fill_luma > paper_luma + 8)
    general_inside = inside & ~(red_or_brown_artifact | overly_bright_patch)

    corrected[general_inside] = corrected[general_inside] * 0.72 + paper_color * 0.28
    strong_tone_mask = red_or_brown_artifact | overly_bright_patch
    corrected[strong_tone_mask] = corrected[strong_tone_mask] * 0.18 + paper_color * 0.82
    dark_inside = inside & (value < 80)
    corrected[dark_inside] = corrected[dark_inside] * 0.45 + paper_color * 0.55
    return np.clip(corrected, 0, 255).astype(np.uint8)


def build_background_raster_image(
    non_text_raster: Image.Image,
    visual_regions: list[dict],
) -> Image.Image:
    image = non_text_raster.convert("RGBA")
    rgba = np.array(image)
    mask = build_visual_region_removal_mask(image, visual_regions)

    if np.any(mask):
        fill = low_frequency_background_fill(image, mask)
        paper_color = estimate_paper_background_color(image, mask)
        paper_fill = smooth_paper_background_fill(image, mask, paper_color)
        fill = (
            fill.astype(np.float32) * 0.26 + paper_fill.astype(np.float32) * 0.74
        ).astype(np.uint8)
        fill = tone_correct_background_fill(fill, mask, paper_color)
        feather = cv2.GaussianBlur(mask, (0, 0), sigmaX=2.0, sigmaY=2.0).astype(
            np.float32
        )
        alpha = np.clip(feather / 255.0, 0.0, 1.0)[:, :, None]
        rgba[:, :, :3] = (
            rgba[:, :, :3].astype(np.float32) * (1.0 - alpha)
            + fill.astype(np.float32) * alpha
        ).astype(np.uint8)
        rgba[:, :, 3] = 255

    return Image.fromarray(rgba, mode="RGBA")


def strip_visual_region_for_json(region: dict) -> dict:
    return {
        key: value
        for key, value in region.items()
        if key != "image"
    }


def write_reference_images(rendered_slides: list[dict], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for slide in rendered_slides:
        reference_path = output_dir / f"{slide['id']}.png"
        composite_slide_reference(slide).save(reference_path)


def choose_recommended_mode(slide: dict) -> str:
    usable_text_blocks = sum(
        1
        for candidate in slide["text_candidates"]
        if is_editable_text_candidate(candidate)
    )
    total_text_blocks = max(1, len(slide["text_candidates"]))
    usable_ratio = usable_text_blocks / float(total_text_blocks)
    if slide["analysis"]["profile"] == "hybrid_review":
        return "hybrid-safe"
    if usable_text_blocks >= 6 or usable_ratio >= 0.55:
        return "vector"
    return "vector"


def choose_recommended_workflow(slide: dict) -> str:
    recommended_mode = choose_recommended_mode(slide)
    if recommended_mode == "hybrid-safe":
        return (
            "Use the hybrid-safe import so the original visual design stays intact, "
            "then rebuild or reveal editable text as needed."
        )
    return (
        "Use the text-edit import so non-text visuals stay as raster while recognized "
        "text becomes editable draft layers."
    )


def build_slide_manifest_entry(slide: dict) -> dict:
    vector_svg = f"slides/{slide['id']}.svg"
    hybrid_svg = f"slides/{slide['id']}.hybrid.svg"
    recommended_mode = choose_recommended_mode(slide)
    recommended_svg = hybrid_svg if recommended_mode == "hybrid-safe" else vector_svg
    non_text_raster = build_non_text_raster_image(slide)
    visual_regions = build_movable_visual_regions(slide, non_text_raster)
    return {
        "id": slide["id"],
        "name": slide["name"],
        "svg": vector_svg,
        "vector_svg": vector_svg,
        "hybrid_svg": hybrid_svg,
        "recommended_svg": recommended_svg,
        "reference_png": f"references/{slide['id']}.png",
        "recommended_workflow": choose_recommended_workflow(slide),
        "canvas": {
            "width": round(slide["width"], 2),
            "height": round(slide["height"], 2),
        },
        "element_count": len(slide["elements"]),
        "vector_layer_count": slide["vector_layer_count"],
        "vector_path_count": slide["vector_path_count"],
        "text_candidate_count": len(slide["text_candidates"]),
        "raster_region_count": len(slide["raster_regions"]),
        "text_candidates": slide["text_candidates"],
        "raster_regions": [
            {
                key: value
                for key, value in region.items()
                if key != "image_base64"
            }
            for region in slide["raster_regions"]
        ],
        "visual_region_count": len(visual_regions),
        "visual_regions": [
            {
                key: value
                for key, value in region.items()
                if key not in {"image", "image_base64"}
            }
            for region in visual_regions
        ],
        "analysis": slide["analysis"],
    }


def build_figma_import_slide_entry(slide: dict) -> dict:
    reference_image = composite_slide_reference(slide)
    non_text_raster = build_non_text_raster_image(slide)
    visual_regions = build_movable_visual_regions(slide, non_text_raster)
    background_raster = build_background_raster_image(non_text_raster, visual_regions)
    recommended_mode = choose_recommended_mode(slide)
    return {
        "id": slide["id"],
        "name": slide["name"],
        "canvas": {
            "width": round(slide["width"], 2),
            "height": round(slide["height"], 2),
        },
        "recommended_mode": recommended_mode,
        "recommended_workflow": choose_recommended_workflow(slide),
        "analysis": slide["analysis"],
        "text_candidates": slide["text_candidates"],
        "raster_regions": slide["raster_regions"],
        "visual_regions": [strip_visual_region_for_json(region) for region in visual_regions],
        "assets": {
            "vector_svg": build_rendered_slide_svg(slide),
            "vector_base_svg": build_rendered_slide_svg(
                slide, include_guides=False, include_raster_regions=False
            ),
            "hybrid_svg": build_hybrid_slide_svg(slide),
            "reference_png_base64": image_to_base64_png(reference_image),
            "non_text_raster_png_base64": image_to_base64_png(non_text_raster),
            "background_raster_png_base64": image_to_base64_png(background_raster),
        },
    }


def write_manifest(
    source_path: Path,
    rendered_slides: list[dict],
    output_dir: Path,
    options: argparse.Namespace,
) -> None:
    manifest = {
        "tool": "SlideRefine prototype",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": str(source_path),
        "mode": options.mode,
        "target": "Figma Design / Figma Slides",
        "text_handling": options.text_handling,
        "notes": [
            "Import the SVGs in slides/ directly into Figma for editable vector layers.",
            "Text candidates are emitted as guide rectangles and listed in this manifest.",
            "Use the reference PNGs when rebuilding text as real Figma text layers.",
            "Hybrid SVGs embed the original raster to preserve the source design while keeping editable guides.",
            "figma-import.json is the plugin-friendly single-file handoff.",
        ],
        "options": {
            "colors": options.colors,
            "min_area": options.min_area,
            "simplify": options.simplify,
            "blur": options.blur,
            "alpha_threshold": options.alpha_threshold,
            "merge_distance": options.merge_distance,
            "skip_text_detection": bool(options.skip_text_detection),
            "text_padding": options.text_padding,
            "ocr_engine": options.ocr_engine,
        },
        "slides": [build_slide_manifest_entry(slide) for slide in rendered_slides],
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )


def write_figma_import_json(
    source_path: Path,
    rendered_slides: list[dict],
    output_dir: Path,
    options: argparse.Namespace,
) -> None:
    payload = {
        "format": {
            "name": "slide-refine-figma-import",
            "version": 1,
        },
        "tool": "SlideRefine prototype",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": str(source_path),
        "default_import_mode": "recommended",
        "options": {
            "text_handling": options.text_handling,
            "ocr_engine": options.ocr_engine,
        },
        "slides": [build_figma_import_slide_entry(slide) for slide in rendered_slides],
    }
    (output_dir / "figma-import.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=True),
        encoding="utf-8",
    )


def write_handoff_markdown(output_dir: Path, rendered_slides: list[dict]) -> None:
    lines = [
        "# Figma Handoff",
        "",
        "This bundle is optimized for Figma-first editing, not pixel-perfect raster reconstruction.",
        "",
        "## Import Flow",
        "",
        "1. Open Figma Design or Figma Slides.",
        "2. For the fastest workflow, use the local SlideRefine importer plugin with `figma-import.json`.",
        "3. Without the plugin, drag the files from `slides/` onto the canvas or into a Slides file.",
        "4. Use `references/*.png` as visual comparison layers if needed.",
        "5. Rebuild text using the dashed orange `text-candidates` guides and the positions from `manifest.json`.",
        "",
        "## Bundle Contents",
        "",
        "- `slides/*.svg`: editable vector regions grouped for Figma.",
        "- `slides/*.hybrid.svg`: exact-design handoff assets with embedded raster reference plus editable guides.",
        "- `raster_regions` in manifest/import JSON: photo-like subregions preserved as image layers.",
        "- `references/*.png`: raster references for manual cleanup.",
        "- `manifest.json`: slide sizes, text candidate boxes, and vector counts.",
        "- `figma-import.json`: single-file payload for the local SlideRefine importer plugin.",
        "",
        "## Notes",
        "",
        "- Vector regions are grouped by dominant color blocks.",
        "- Text is excluded from vectorization by default to avoid hard-baked glyph paths.",
        "- OCR-backed text blocks are written to the manifest when Windows OCR is available.",
        "- The importer plugin can place raster-safe slides without changing the visible design.",
        "- The current MVP favors editability over exact fidelity.",
        "",
        "## Slide Summary",
        "",
    ]

    for slide in rendered_slides:
        recommended_mode = choose_recommended_mode(slide)
        lines.append(
            f"- `{slide['id']}`: {slide['vector_layer_count']} vector layers, "
            f"{slide['vector_path_count']} paths, {len(slide['text_candidates'])} text candidates, "
            f"{len(slide['raster_regions'])} raster regions, "
            f"profile `{slide['analysis']['profile']}`."
        )
        lines.append(f"  Recommendation: {choose_recommended_workflow(slide)}")
        recommended_asset = (
            f"`slides/{slide['id']}.hybrid.svg`"
            if recommended_mode == "hybrid-safe"
            else f"`slides/{slide['id']}.svg`"
        )
        lines.append(f"  Recommended asset: {recommended_asset}")

    (output_dir / "handoff.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_svg_outputs(
    source_path: Path,
    rendered_slides: list[dict],
    output_path: Path,
) -> None:
    single_svg = (
        len(rendered_slides) == 1
        and not source_path.is_dir()
        and source_path.suffix.lower() in IMAGE_EXTENSIONS
        and output_path.suffix.lower() == ".svg"
    )

    if single_svg:
        write_rendered_slide_svg(rendered_slides[0], output_path)
        return

    output_path.mkdir(parents=True, exist_ok=True)
    for slide in rendered_slides:
        write_rendered_slide_svg(slide, output_path / f"{slide['id']}.svg")


def write_package_output(
    source_path: Path,
    rendered_slides: list[dict],
    output_dir: Path,
    options: argparse.Namespace,
) -> None:
    slides_dir = output_dir / "slides"
    references_dir = output_dir / "references"
    slides_dir.mkdir(parents=True, exist_ok=True)

    for slide in rendered_slides:
        write_rendered_slide_svg(slide, slides_dir / f"{slide['id']}.svg")
        write_hybrid_slide_svg(slide, slides_dir / f"{slide['id']}.hybrid.svg")

    write_reference_images(rendered_slides, references_dir)
    write_manifest(source_path, rendered_slides, output_dir, options)
    write_figma_import_json(source_path, rendered_slides, output_dir, options)
    write_handoff_markdown(output_dir, rendered_slides)


def default_output_path(source_path: Path, mode: str) -> Path:
    base_name = source_path.stem if source_path.is_file() else source_path.name

    if mode == "package":
        return source_path.parent / f"{base_name}-figma-package"

    if source_path.is_dir() or source_path.suffix.lower() == ".pptx":
        return source_path.parent / f"{base_name}-svg"
    return source_path.with_suffix(".svg")


def main() -> int:
    options = parse_args()
    source_path = Path(options.input).expanduser().resolve()
    if not source_path.exists():
        print(f"Input not found: {source_path}", file=sys.stderr)
        return 1

    output_path = (
        Path(options.output).expanduser().resolve()
        if options.output
        else default_output_path(source_path, options.mode)
    )

    try:
        source_slides = load_source_slides(source_path)
        rendered_slides = [
            render_slide(slide=slide, options=options) for slide in source_slides
        ]

        if options.mode == "package":
            write_package_output(
                source_path=source_path,
                rendered_slides=rendered_slides,
                output_dir=output_path,
                options=options,
            )
            print(f"Generated Figma package in {output_path}")
            return 0

        write_svg_outputs(
            source_path=source_path,
            rendered_slides=rendered_slides,
            output_path=output_path,
        )
        print(f"Generated SVG output in {output_path}")
        return 0
    except Exception as exc:  # pragma: no cover - CLI fallback
        print(f"Conversion failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
