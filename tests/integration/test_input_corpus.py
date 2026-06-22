from __future__ import annotations

from pathlib import Path

import pytest

from sliderefine.application.conversion_service import ConversionOptions, convert
from sliderefine.domain.document import validate_document
from sliderefine.exporters.srf import load_document, save_document
from sliderefine.exporters.svg import export_svg


def test_directory_fixture_converts_to_srf_and_svg(tmp_path: Path):
    result = convert("tests/fixtures/slides-dir", ConversionOptions(ocr_engine="none", skip_text_detection=True))
    assert len(result.document["slides"]) == 2
    assert not validate_document(result.document)
    document_path = tmp_path / "slides-dir.srf"
    save_document(result.document, document_path)
    loaded = load_document(document_path)
    export_svg(loaded, tmp_path / "slides-dir.svg", archive_path=document_path)
    assert (tmp_path / "slides-dir.svg").stat().st_size > 0


def test_image_backed_pptx_fixture_converts_to_srf_and_svg(tmp_path: Path):
    result = convert("tests/fixtures/image-backed.pptx", ConversionOptions(ocr_engine="none", skip_text_detection=True))
    assert len(result.document["slides"]) == 1
    assert not validate_document(result.document)
    document_path = tmp_path / "deck.srf"
    save_document(result.document, document_path)
    loaded = load_document(document_path)
    export_svg(loaded, tmp_path / "deck.svg", archive_path=document_path)
    assert (tmp_path / "deck.svg").stat().st_size > 0


def test_source_file_size_limit_rejects_before_parse(fixture_image: Path):
    with pytest.raises(ValueError, match="Source file is .* limit"):
        convert(
            fixture_image,
            ConversionOptions(
                ocr_engine="none",
                skip_text_detection=True,
                max_source_bytes=1,
            ),
        )
