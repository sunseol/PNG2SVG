from __future__ import annotations

from pathlib import Path

from tools.release_gate import REQUIRED_EVIDENCE


def test_license_notice_and_supply_chain_gate_are_declared():
    assert Path("LICENSE").read_text(encoding="utf-8").strip()
    assert Path("NOTICE").read_text(encoding="utf-8").strip()
    assert "supplyChain" in REQUIRED_EVIDENCE
    assert {
        "supply-chain/report.json",
        "supply-chain/python-audit.json",
        "supply-chain/sbom.cyclonedx.json",
        "supply-chain/npm-audit.json",
    } <= set(REQUIRED_EVIDENCE["supplyChain"])
