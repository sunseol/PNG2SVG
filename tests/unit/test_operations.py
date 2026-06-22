from __future__ import annotations

import pytest

from sliderefine.application.conversion_service import ConversionOptions, convert
from sliderefine.application.operation_service import OperationError, apply_transaction
from sliderefine.exporters.svg import build_svg


def test_translate_dry_run_does_not_change_revision(fixture_image):
    document = convert(fixture_image, ConversionOptions(ocr_engine="none", skip_text_detection=True)).document
    node_id = next(iter(document["nodes"]))
    updated, result = apply_transaction(
        document,
        {
            "schemaVersion": "1.0.0",
            "operationId": "op-dry",
            "expectedRevision": document["revision"],
            "dryRun": True,
            "operations": [{"type": "translate", "nodeIds": [node_id], "dx": 2, "dy": 3}],
        },
    )
    assert updated is document
    assert result["revision"] == document["revision"]


def test_revision_conflict(fixture_image):
    document = convert(fixture_image, ConversionOptions(ocr_engine="none", skip_text_detection=True)).document
    node_id = next(iter(document["nodes"]))
    with pytest.raises(OperationError) as error:
        apply_transaction(
            document,
            {
                "schemaVersion": "1.0.0",
                "operationId": "op-conflict",
                "expectedRevision": 99,
                "operations": [{"type": "translate", "nodeIds": [node_id], "dx": 1, "dy": 1}],
            },
        )
    assert error.value.code == "REVISION_CONFLICT"


def test_repeated_operation_id_is_idempotent(fixture_image):
    document = convert(fixture_image, ConversionOptions(ocr_engine="none", skip_text_detection=True)).document
    node_id = next(iter(document["nodes"]))
    transaction = {
        "schemaVersion": "1.0.0",
        "operationId": "op-idempotent",
        "expectedRevision": document["revision"],
        "operations": [{"type": "translate", "nodeIds": [node_id], "dx": 5, "dy": 0}],
    }
    first, first_result = apply_transaction(document, transaction)
    second, second_result = apply_transaction(first, transaction)
    assert second is first
    assert second_result == first_result
    assert first["revision"] == document["revision"] + 1
    assert first["nodes"][node_id]["transform"][4] == document["nodes"][node_id]["transform"][4] + 5


def test_rotate_and_lock_export_to_svg(fixture_image):
    document = convert(fixture_image, ConversionOptions(ocr_engine="none", skip_text_detection=True)).document
    node_id = next(node_id for node_id, node in document["nodes"].items() if node["type"] != "image")
    updated, result = apply_transaction(
        document,
        {
            "schemaVersion": "1.0.0",
            "operationId": "op-rotate-lock",
            "expectedRevision": document["revision"],
            "operations": [
                {"type": "rotate", "nodeId": node_id, "degrees": 15},
                {"type": "set_locked", "nodeId": node_id, "locked": True},
            ],
        },
    )
    assert result["changedNodeIds"] == [node_id]
    assert updated["nodes"][node_id]["extensions"]["rotationDegrees"] == 15
    assert updated["nodes"][node_id]["locked"] is True
    assert "rotate(15" in build_svg(updated)
