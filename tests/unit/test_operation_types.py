from __future__ import annotations

from sliderefine.application.conversion_service import ConversionOptions, convert
from sliderefine.application.operation_service import apply_transaction


def test_group_and_ungroup(fixture_image):
    document = convert(fixture_image, ConversionOptions(ocr_engine="none", skip_text_detection=True)).document
    node_ids = list(document["nodes"])[:2]
    grouped, result = apply_transaction(
        document,
        {
            "schemaVersion": "1.0.0",
            "operationId": "op-group",
            "expectedRevision": document["revision"],
            "operations": [{"type": "group", "nodeIds": node_ids, "groupId": "group-test"}],
        },
    )
    assert "group-test" in grouped["nodes"]
    assert result["revision"] == 2

    ungrouped, result = apply_transaction(
        grouped,
        {
            "schemaVersion": "1.0.0",
            "operationId": "op-ungroup",
            "expectedRevision": grouped["revision"],
            "operations": [{"type": "ungroup", "nodeId": "group-test"}],
        },
    )
    assert "group-test" not in ungrouped["nodes"]
    assert result["revision"] == 3
