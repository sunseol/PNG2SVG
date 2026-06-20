from __future__ import annotations

import json
import zipfile

from sliderefine.application.conversion_service import ConversionOptions, convert
from sliderefine.domain.document import sha256_bytes, validate_document
from sliderefine.exporters.srf import load_document, save_document


def test_srf_roundtrip_has_no_base64_document_json(fixture_image, tmp_path):
    result = convert(fixture_image, ConversionOptions(ocr_engine="none", skip_text_detection=True))
    assert not validate_document(result.document)

    output = tmp_path / "fixture.srf"
    save_document(result.document, output)
    loaded = load_document(output)
    assert loaded["documentId"] == result.document["documentId"]
    assert loaded["assets"]

    with zipfile.ZipFile(output) as archive:
        document_json = archive.read("document.json").decode("utf-8")
    assert "base64" not in document_json.lower()
    assert "_bytes" not in document_json


def test_deterministic_srf_hash(fixture_image, tmp_path):
    first = convert(fixture_image, ConversionOptions(ocr_engine="none", skip_text_detection=True))
    second = convert(fixture_image, ConversionOptions(ocr_engine="none", skip_text_detection=True))
    first_path = tmp_path / "a.srf"
    second_path = tmp_path / "b.srf"
    save_document(first.document, first_path)
    save_document(second.document, second_path)
    assert first_path.read_bytes() == second_path.read_bytes()


def test_legacy_document_migrates_to_current_schema(tmp_path):
    legacy_document = {
        "schemaVersion": "0.9.0",
        "documentId": "doc-legacy",
        "revision": 1,
        "source": {"kind": "image", "uri": (tmp_path / "legacy.png").as_uri(), "digest": "sha256:" + "0" * 64},
        "slides": {
            "slide-001": {
                "id": "slide-001",
                "name": "Legacy slide",
                "width": 100,
                "height": 100,
                "children": ["rect-001"],
            }
        },
        "nodes": {
            "rect-001": {
                "id": "rect-001",
                "type": "rect",
                "name": "Legacy rect",
                "parentId": "slide-001",
                "bounds": {"x": 0, "y": 0, "width": 10, "height": 10},
                "fill": {"type": "solid", "color": "#000000", "opacity": 1},
            }
        },
        "assets": {},
    }
    document_bytes = json.dumps(legacy_document, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    manifest = {
        "format": {"name": "slide-refine-document", "version": "0.9.0"},
        "documentPath": "document.json",
        "documentSha256": sha256_bytes(document_bytes),
        "assets": {},
    }
    archive_path = tmp_path / "legacy.srf"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr("document.json", document_bytes)

    migrated = load_document(archive_path)

    assert migrated["schemaVersion"] == "1.0.0"
    assert migrated["diagnostics"] == []
    assert migrated["operationLog"] == []
    assert migrated["nodes"]["rect-001"]["visible"] is True
    assert migrated["nodes"]["rect-001"]["provenance"]["stage"] == "migration"
    assert not validate_document(migrated)

    migrated_path = tmp_path / "migrated.srf"
    save_document(migrated, migrated_path)
    assert load_document(migrated_path)["schemaVersion"] == "1.0.0"
