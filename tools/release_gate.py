from __future__ import annotations

import argparse
import json
import subprocess
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path


REQUIRED_EVIDENCE = {
    "environment": ["environment.json"],
    "sourceTree": ["source-tree.txt"],
    "hashes": ["hashes.sha256"],
    "typecheck": ["typecheck/report.json"],
    "unit": ["unit/junit.xml"],
    "contract": ["contract/report.json"],
    "cliE2E": ["cli/report.json"],
    "golden": ["golden/report.json"],
    "visual": ["visual/metrics.json"],
    "browser": ["browser/report.json", "browser/playwright-report"],
    "mcp": ["mcp/scenario-report.json", "mcp/inspector-transcript.jsonl"],
    "security": ["security/report.json"],
    "performance": ["performance/benchmark.json"],
    "packages": ["packages/wheel.sha256", "packages/web-bundle.sha256"],
    "supplyChain": [
        "supply-chain/report.json",
        "supply-chain/python-audit.json",
        "supply-chain/sbom.cyclonedx.json",
        "supply-chain/npm-audit.json",
    ],
}

DATA_ONLY_JSON_EVIDENCE = {
    "supply-chain/python-audit.json",
    "supply-chain/sbom.cyclonedx.json",
    "supply-chain/npm-audit.json",
}


def _git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except Exception:
        return "unknown"


def _dirty_worktree() -> bool:
    try:
        output = subprocess.check_output(["git", "status", "--porcelain"], text=True)
        return bool(output.strip())
    except Exception:
        return True


def _json_status(path: Path) -> str | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    status = payload.get("status") or payload.get("overall")
    if status == "ok" or status == "pass":
        return "pass"
    if status == "fail" or status == "error":
        return "fail"
    return None


def _json_parseable(path: Path) -> bool:
    try:
        json.loads(path.read_text(encoding="utf-8"))
        return True
    except Exception:
        return False


def _junit_status(path: Path) -> str | None:
    try:
        root = ET.fromstring(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    failures = int(root.attrib.get("failures", "0"))
    errors = int(root.attrib.get("errors", "0"))
    return "pass" if failures == 0 and errors == 0 else "fail"


def _evidence_status(evidence: Path, candidates: list[str]) -> tuple[str, list[str]]:
    missing = []
    failures = []
    for candidate in candidates:
        path = evidence / candidate
        if not path.exists():
            missing.append(candidate)
            continue
        if path.is_dir():
            continue
        status: str | None
        if path.suffix == ".xml":
            status = _junit_status(path)
        elif path.suffix == ".json":
            status = "pass" if candidate in DATA_ONLY_JSON_EVIDENCE and _json_parseable(path) else _json_status(path)
        else:
            status = "pass"
        if status != "pass":
            failures.append(candidate)
    if missing or failures:
        return "fail", missing + failures
    return "pass", []


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    evidence = Path(args.evidence)
    evidence.mkdir(parents=True, exist_ok=True)
    gates = {}
    missing = {}
    for gate, candidates in REQUIRED_EVIDENCE.items():
        status, missing_candidates = _evidence_status(evidence, candidates)
        gates[gate] = status
        if missing_candidates:
            missing[gate] = missing_candidates
    dirty = _dirty_worktree()
    if dirty:
        gates["sourceClean"] = "fail"
        missing["sourceClean"] = ["git status --porcelain must be empty for release evidence"]
    else:
        gates["sourceClean"] = "pass"
    verdict = {
        "gitSha": _git_sha(),
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "gates": gates,
        "exceptions": [],
        "missingEvidence": missing,
        "dirtyWorktree": dirty,
        "overall": "pass" if all(value == "pass" for value in gates.values()) else "fail",
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(verdict, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if verdict["overall"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
