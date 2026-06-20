from __future__ import annotations

import json
import zipfile

import pytest

from sliderefine.domain.document import (
    ValidationError,
    add_node,
    add_slide,
    base_node,
    make_bounds,
    new_document,
    sha256_bytes,
    validate_document,
)
from sliderefine.exporters.srf import load_document


def test_zip_traversal_rejected(tmp_path):
    bad = tmp_path / "bad.srf"
    with zipfile.ZipFile(bad, "w") as archive:
        archive.writestr("../escape.txt", "bad")
        archive.writestr("manifest.json", "{}")
    with pytest.raises(ValidationError):
        load_document(bad)


def test_zip_absolute_path_rejected(tmp_path):
    bad = tmp_path / "absolute.srf"
    with zipfile.ZipFile(bad, "w") as archive:
        archive.writestr("/absolute.txt", "bad")
        archive.writestr("manifest.json", "{}")
    with pytest.raises(ValidationError):
        load_document(bad)


def test_zip_high_compression_ratio_rejected(tmp_path):
    bad = tmp_path / "ratio.srf"
    with zipfile.ZipFile(bad, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", "{}")
        archive.writestr("document.json", "0" * 1_000_000)
    with pytest.raises(ValidationError):
        load_document(bad)


def test_malformed_json_rejected(tmp_path):
    bad = tmp_path / "malformed-json.srf"
    with zipfile.ZipFile(bad, "w") as archive:
        archive.writestr("manifest.json", "{not-json")
        archive.writestr("document.json", "{}")
    with pytest.raises(ValidationError, match="Malformed SRF archive"):
        load_document(bad)


def test_huge_slide_dimensions_rejected(tmp_path):
    document = new_document(tmp_path / "source.png", "image", "0" * 64)
    add_slide(document, "slide-huge", "Huge", 1_000_000, 1_000_000)
    errors = validate_document(document)
    assert any(error["code"] in {"SLIDE_DIMENSION_LIMIT", "SLIDE_PIXEL_LIMIT"} for error in errors)


def test_svg_script_asset_rejected(tmp_path):
    document = new_document(tmp_path / "source.png", "image", "0" * 64)
    add_slide(document, "slide-001", "Slide", 100, 100)
    asset_bytes = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    asset_sha = sha256_bytes(asset_bytes)
    asset_id = f"sha256:{asset_sha}"
    asset_path = f"assets/sha256-{asset_sha}.svg"
    document["assets"][asset_id] = {
        "id": asset_id,
        "mimeType": "image/svg+xml",
        "byteLength": len(asset_bytes),
        "sha256": asset_sha,
        "path": asset_path,
    }
    image = base_node("image-svg", "image", "SVG image", "slide-001", make_bounds(0, 0, 10, 10))
    image.update({"assetId": asset_id, "crop": None, "mask": None, "reason": "malicious-svg"})
    add_node(document, image)

    document_bytes = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    manifest = {
        "format": {"name": "slide-refine-document", "version": "1.0.0"},
        "documentPath": "document.json",
        "documentSha256": sha256_bytes(document_bytes),
        "assets": {
            asset_id: {
                "path": asset_path,
                "mimeType": "image/svg+xml",
                "byteLength": len(asset_bytes),
                "sha256": asset_sha,
            }
        },
    }
    archive_path = tmp_path / "malicious-svg.srf"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr("document.json", document_bytes)
        archive.writestr(asset_path, asset_bytes)

    with pytest.raises(ValidationError, match="ACTIVE_ASSET_MIME_TYPE"):
        load_document(archive_path)
