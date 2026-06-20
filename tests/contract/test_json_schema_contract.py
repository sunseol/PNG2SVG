from __future__ import annotations

import json
from pathlib import Path

import jsonschema
from jsonschema import Draft202012Validator

from sliderefine.application.conversion_service import ConversionOptions, convert
from sliderefine.cli.main import _ok


SCHEMA_DIR = Path("schema")


def load_schema(name: str) -> dict:
    return json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))


def validator(name: str) -> Draft202012Validator:
    schema = load_schema(name)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def assert_valid(name: str, instance: dict) -> None:
    validator(name).validate(instance)


def test_schema_files_are_valid_draft_2020_12():
    for path in sorted(SCHEMA_DIR.glob("*.schema.json")):
        Draft202012Validator.check_schema(json.loads(path.read_text(encoding="utf-8")))


def test_generated_document_matches_scene_document_schema(fixture_image):
    result = convert(fixture_image, ConversionOptions(ocr_engine="none", skip_text_detection=True))

    assert_valid("document.schema.json", result.document)


def test_operation_fixture_matches_operation_schema():
    transaction = json.loads(Path("tests/fixtures/ops/edit-title.json").read_text(encoding="utf-8"))

    assert_valid("operations.schema.json", transaction)


def test_cli_result_matches_result_schema():
    payload = _ok("doctor", version="0.1.0")

    assert_valid("cli-result.schema.json", payload)


def test_conversion_and_export_options_match_schemas():
    assert_valid(
        "conversion-options.schema.json",
        {
            "colors": 12,
            "ocrEngine": "none",
            "deterministic": True,
            "maxSlides": 100,
            "maxImagePixels": 100_000_000,
            "maxSourceBytes": 256 * 1024 * 1024,
            "timeoutMs": 1000,
        },
    )
    assert_valid("export-options.schema.json", {"format": "svg", "includeGuides": True, "scale": 1})


def test_schema_rejects_unknown_top_level_document_fields(fixture_image):
    result = convert(fixture_image, ConversionOptions(ocr_engine="none", skip_text_detection=True))
    invalid = {**result.document, "unexpected": True}

    try:
        assert_valid("document.schema.json", invalid)
    except jsonschema.ValidationError:
        return
    raise AssertionError("document schema accepted an unknown top-level field")
