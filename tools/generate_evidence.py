from __future__ import annotations

import argparse
import hashlib
import io
import json
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from shutil import which

import cv2
import numpy as np
from PIL import Image, ImageChops

from sliderefine.application.conversion_service import ConversionOptions, convert
from sliderefine.exporters.png import render_png
from sliderefine.exporters.srf import read_asset_bytes


ROOT = Path(__file__).resolve().parents[1]
VISUAL_MIN_MEDIAN_SSIM = 0.92
VISUAL_MIN_P10_SSIM = 0.82
VISUAL_MAX_MEAN_ABS_DIFF = 0.12


def os_environ() -> dict[str, str]:
    import os

    return dict(os.environ)


def npm_command() -> str:
    found = which("npm") or which("npm.cmd")
    if found:
        return found
    candidates = [
        Path("C:/Program Files/nodejs/npm.cmd"),
        Path("C:/Program Files (x86)/nodejs/npm.cmd"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return "npm"


def run(command: list[str], *, cwd: Path = ROOT, env: dict[str, str] | None = None) -> dict:
    started = datetime.now(timezone.utc)
    completed = subprocess.run(command, cwd=cwd, text=True, capture_output=True, env=env)
    return {
        "command": command,
        "cwd": str(cwd),
        "startedAt": started.isoformat(),
        "completedAt": datetime.now(timezone.utc).isoformat(),
        "exitCode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "status": "ok" if completed.returncode == 0 else "fail",
    }


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_sha() -> str:
    result = run(["git", "rev-parse", "HEAD"])
    return result["stdout"].strip() if result["status"] == "ok" else "unknown"


def write_environment(evidence: Path) -> None:
    payload = {
        "status": "ok",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "gitSha": git_sha(),
        "python": sys.version,
        "platform": sys.platform,
        "cwd": str(ROOT),
    }
    write_json(evidence / "environment.json", payload)


def write_source_tree(evidence: Path) -> None:
    result = run(["git", "ls-files"])
    if result["status"] == "ok":
        content = result["stdout"]
    else:
        content = "\n".join(str(path.relative_to(ROOT)) for path in ROOT.rglob("*") if path.is_file())
    (evidence / "source-tree.txt").write_text(content, encoding="utf-8")


def write_hashes(evidence: Path) -> None:
    files = []
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(ROOT)
        if rel.parts[0] in {".git", ".artifacts", "build", "dist"}:
            continue
        if rel.parts[0] == "evidence":
            continue
        if "node_modules" in rel.parts:
            continue
        files.append(f"{sha256(path)}  {rel.as_posix()}")
    (evidence / "hashes.sha256").write_text("\n".join(files) + "\n", encoding="utf-8")


def cli_e2e(evidence: Path) -> dict:
    artifacts = ROOT / ".artifacts" / "evidence-cli"
    artifacts.mkdir(parents=True, exist_ok=True)
    commands = [
        [sys.executable, "-m", "sliderefine.cli.main", "doctor", "--json"],
        [sys.executable, "-m", "sliderefine.cli.main", "capabilities", "--json"],
        [
            sys.executable,
            "-m",
            "sliderefine.cli.main",
            "convert",
            "tests/fixtures/shape-heavy.png",
            "-o",
            str(artifacts / "shape-heavy.srf"),
            "--deterministic",
            "--json",
        ],
        [
            sys.executable,
            "-m",
            "sliderefine.cli.main",
            "convert",
            "tests/fixtures/slides-dir",
            "-o",
            str(artifacts / "slides-dir.srf"),
            "--deterministic",
            "--json",
        ],
        [
            sys.executable,
            "-m",
            "sliderefine.cli.main",
            "convert",
            "tests/fixtures/image-backed.pptx",
            "-o",
            str(artifacts / "image-backed.srf"),
            "--deterministic",
            "--json",
        ],
        [sys.executable, "-m", "sliderefine.cli.main", "validate", str(artifacts / "shape-heavy.srf"), "--json"],
        [sys.executable, "-m", "sliderefine.cli.main", "validate", str(artifacts / "slides-dir.srf"), "--json"],
        [sys.executable, "-m", "sliderefine.cli.main", "validate", str(artifacts / "image-backed.srf"), "--json"],
        [sys.executable, "-m", "sliderefine.cli.main", "inspect", str(artifacts / "shape-heavy.srf"), "--json"],
        [
            sys.executable,
            "-m",
            "sliderefine.cli.main",
            "apply",
            str(artifacts / "shape-heavy.srf"),
            "--operations",
            "tests/fixtures/ops/edit-title.json",
            "-o",
            str(artifacts / "shape-heavy-edited.srf"),
            "--json",
        ],
        [
            sys.executable,
            "-m",
            "sliderefine.cli.main",
            "render",
            str(artifacts / "shape-heavy-edited.srf"),
            "--slide",
            "slide-01",
            "-o",
            str(artifacts / "preview.png"),
            "--json",
        ],
        [
            sys.executable,
            "-m",
            "sliderefine.cli.main",
            "export",
            str(artifacts / "shape-heavy-edited.srf"),
            "--format",
            "svg",
            "-o",
            str(artifacts / "result.svg"),
            "--json",
        ],
    ]
    results = [run(command) for command in commands]
    report = {
        "status": "ok" if all(result["status"] == "ok" for result in results) else "fail",
        "artifacts": {path.name: sha256(path) for path in artifacts.glob("*") if path.is_file()},
        "commands": results,
    }
    write_json(evidence / "cli" / "report.json", report)
    return report


def golden(evidence: Path) -> dict:
    artifacts = ROOT / ".artifacts" / "evidence-golden"
    artifacts.mkdir(parents=True, exist_ok=True)
    first = artifacts / "run-a.srf"
    second = artifacts / "run-b.srf"
    commands = [
        [
            sys.executable,
            "-m",
            "sliderefine.cli.main",
            "convert",
            "tests/fixtures/shape-heavy.png",
            "-o",
            str(first),
            "--deterministic",
            "--json",
        ],
        [
            sys.executable,
            "-m",
            "sliderefine.cli.main",
            "convert",
            "tests/fixtures/shape-heavy.png",
            "-o",
            str(second),
            "--deterministic",
            "--json",
        ],
    ]
    results = [run(command) for command in commands]
    hashes_match = first.exists() and second.exists() and sha256(first) == sha256(second)
    report = {
        "status": "ok" if all(result["status"] == "ok" for result in results) and hashes_match else "fail",
        "hashesMatch": hashes_match,
        "runA": sha256(first) if first.exists() else None,
        "runB": sha256(second) if second.exists() else None,
        "commands": results,
    }
    write_json(evidence / "golden" / "report.json", report)
    return report


def visual(evidence: Path) -> dict:
    visual_dir = evidence / "visual"
    original_dir = visual_dir / "original"
    rendered_dir = visual_dir / "rendered"
    diff_dir = visual_dir / "diff"
    for directory in (original_dir, rendered_dir, diff_dir):
        directory.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((ROOT / "tests" / "fixtures" / "manifest.json").read_text(encoding="utf-8"))
    cases = []
    scores: list[float] = []
    mean_abs_diffs: list[float] = []
    for case in manifest.get("cases", []):
        result = convert(ROOT / case["input"], ConversionOptions(ocr_engine="none", skip_text_detection=True))
        for slide_id, slide in result.document["slides"].items():
            reference_node = next(
                (
                    node
                    for node in result.document["nodes"].values()
                    if node.get("parentId") == slide_id and node.get("reason") == "original_overlay"
                ),
                None,
            )
            if reference_node is None:
                cases.append({"name": case["name"], "slideId": slide_id, "status": "fail", "reason": "missing reference"})
                continue
            stem = f"{case['name']}-{slide_id}"
            original_path = original_dir / f"{stem}.png"
            rendered_path = rendered_dir / f"{stem}.png"
            diff_path = diff_dir / f"{stem}.png"
            original = Image.open(io.BytesIO(read_asset_bytes(result.document, reference_node["assetId"]))).convert("RGBA")
            original.save(original_path)
            render_png(result.document, rendered_path, slide_id=slide_id, scale=1.0)
            rendered = Image.open(rendered_path).convert("RGBA")
            if rendered.size != original.size:
                rendered = rendered.resize(original.size, Image.Resampling.LANCZOS)
                rendered.save(rendered_path)
            ssim = _ssim(original, rendered)
            mean_abs_diff = _mean_abs_diff(original, rendered)
            diff = ImageChops.difference(original.convert("RGB"), rendered.convert("RGB"))
            diff.save(diff_path)
            scores.append(ssim)
            mean_abs_diffs.append(mean_abs_diff)
            cases.append(
                {
                    "name": case["name"],
                    "slideId": slide_id,
                    "status": "ok",
                    "ssim": round(ssim, 6),
                    "meanAbsDiff": round(mean_abs_diff, 6),
                    "original": str(original_path.relative_to(evidence)),
                    "rendered": str(rendered_path.relative_to(evidence)),
                    "diff": str(diff_path.relative_to(evidence)),
                    "nodeCount": len(result.document["nodes"]),
                    "pathCount": sum(1 for node in result.document["nodes"].values() if node["type"] == "path"),
                }
            )
    median_ssim = _percentile(scores, 50)
    p10_ssim = _percentile(scores, 10)
    worst_mean_abs_diff = max(mean_abs_diffs) if mean_abs_diffs else 1.0
    report = {
        "status": (
            "ok"
            if cases
            and all(case["status"] == "ok" for case in cases)
            and median_ssim >= VISUAL_MIN_MEDIAN_SSIM
            and p10_ssim >= VISUAL_MIN_P10_SSIM
            and worst_mean_abs_diff <= VISUAL_MAX_MEAN_ABS_DIFF
            else "fail"
        ),
        "metrics": {
            "medianSsim": round(median_ssim, 6),
            "p10Ssim": round(p10_ssim, 6),
            "worstMeanAbsDiff": round(worst_mean_abs_diff, 6),
            "thresholds": {
                "medianSsim": VISUAL_MIN_MEDIAN_SSIM,
                "p10Ssim": VISUAL_MIN_P10_SSIM,
                "worstMeanAbsDiff": VISUAL_MAX_MEAN_ABS_DIFF,
            },
            "cases": cases,
        },
    }
    write_json(visual_dir / "metrics.json", report)
    return report


def _rgb_array(image: Image.Image) -> np.ndarray:
    return np.asarray(image.convert("RGB"), dtype=np.float64) / 255.0


def _ssim(first: Image.Image, second: Image.Image) -> float:
    a = _rgb_array(first).mean(axis=2)
    b = _rgb_array(second).mean(axis=2)
    c1 = 0.01**2
    c2 = 0.03**2
    window = (7, 7)
    mu_a = cv2.blur(a, window)
    mu_b = cv2.blur(b, window)
    mu_a_sq = mu_a * mu_a
    mu_b_sq = mu_b * mu_b
    mu_ab = mu_a * mu_b
    var_a = cv2.blur(a * a, window) - mu_a_sq
    var_b = cv2.blur(b * b, window) - mu_b_sq
    covariance = cv2.blur(a * b, window) - mu_ab
    denominator = (mu_a_sq + mu_b_sq + c1) * (var_a + var_b + c2)
    if float(np.abs(denominator).max()) == 0:
        return 1.0 if np.array_equal(a, b) else 0.0
    values = ((2 * mu_ab + c1) * (2 * covariance + c2)) / denominator
    return float(np.nan_to_num(np.clip(values, 0.0, 1.0), nan=0.0).mean())


def _mean_abs_diff(first: Image.Image, second: Image.Image) -> float:
    return float(np.abs(_rgb_array(first) - _rgb_array(second)).mean())


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    return float(np.percentile(np.asarray(values, dtype=np.float64), percentile))


def mcp_report(evidence: Path) -> dict:
    result = run([sys.executable, "-m", "pytest", "tests/mcp", "-q"])
    from sliderefine.mcp.server import _handle

    mcp_dir = evidence / "mcp"
    mcp_dir.mkdir(parents=True, exist_ok=True)
    document_path = mcp_dir / "scenario.srf"
    export_path = mcp_dir / "scenario.svg"
    transcript = []

    def call(message: dict) -> dict:
        response = _handle(message)
        transcript.append({"request": message, "response": response})
        if response is None or "error" in response:
            raise RuntimeError(json.dumps(response, ensure_ascii=False))
        return response

    try:
        call({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        tools = call({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]
        converted = call(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "convert_image",
                    "arguments": {
                        "inputUri": (ROOT / "tests/fixtures/shape-heavy.png").resolve().as_uri(),
                        "outputUri": document_path.resolve().as_uri(),
                        "options": {"skipTextDetection": True},
                    },
                },
            }
        )
        document_uri = converted["result"]["content"][0]["json"]["documentUri"]
        queried = call(
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "query_nodes", "arguments": {"documentUri": document_uri, "types": ["image"], "limit": 1}},
            }
        )
        node_id = queried["result"]["content"][0]["json"]["nodes"][0]["id"]
        call(
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {
                    "name": "apply_operations",
                    "arguments": {
                        "documentUri": document_uri,
                        "operationId": "op-evidence-mcp-dry",
                        "expectedRevision": 1,
                        "dryRun": True,
                        "operations": [{"type": "translate", "nodeIds": [node_id], "dx": 1, "dy": 1}],
                    },
                },
            }
        )
        call(
            {
                "jsonrpc": "2.0",
                "id": 6,
                "method": "tools/call",
                "params": {
                    "name": "apply_operations",
                    "arguments": {
                        "documentUri": document_uri,
                        "operationId": "op-evidence-mcp-apply",
                        "expectedRevision": 1,
                        "operations": [{"type": "translate", "nodeIds": [node_id], "dx": 1, "dy": 1}],
                    },
                },
            }
        )
        preview = call(
            {
                "jsonrpc": "2.0",
                "id": 7,
                "method": "tools/call",
                "params": {"name": "render_preview", "arguments": {"documentUri": document_uri}},
            }
        )
        exported = call(
            {
                "jsonrpc": "2.0",
                "id": 8,
                "method": "tools/call",
                "params": {
                    "name": "export_document",
                    "arguments": {"documentUri": document_uri, "format": "svg", "outputUri": export_path.resolve().as_uri()},
                },
            }
        )
        scenario_status = (
            "ok"
            if result["status"] == "ok"
            and len(tools) >= 6
            and preview["result"]["content"][0]["json"]["preview"]["size"] > 0
            and exported["result"]["content"][0]["json"]["artifact"]["size"] > 0
            else "fail"
        )
        report = {
            "status": scenario_status,
            "command": result,
            "toolCount": len(tools),
            "standardLoop": ["initialize", "tools/list", "convert", "query", "dry-run", "apply", "render", "export"],
        }
    except Exception as exc:
        report = {"status": "fail", "command": result, "error": str(exc)}
    write_json(mcp_dir / "scenario-report.json", report)
    (mcp_dir / "inspector-transcript.jsonl").write_text(
        "\n".join(json.dumps(entry, ensure_ascii=False, sort_keys=True) for entry in transcript) + "\n",
        encoding="utf-8",
    )
    return report


def security_report(evidence: Path) -> dict:
    result = run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/security",
            "tests/integration/test_local_editor_server.py",
            "-q",
        ]
    )
    report = {
        "status": result["status"],
        "command": result,
        "coverage": [
            "srf zip traversal",
            "srf absolute zip path",
            "srf compression ratio",
            "malformed JSON",
            "SVG script asset",
            "huge dimensions",
            "conversion source file size limit",
            "parse/convert timeout",
            "windows OCR subprocess timeout",
            "document node count limit",
            "document group depth limit",
            "document path command limit",
            "document path control-point limit",
            "local editor token",
            "local editor exact origin",
            "local editor CSP/security headers",
        ],
    }
    write_json(evidence / "security" / "report.json", report)
    return report


def package_reports(evidence: Path) -> dict:
    build_result = run([sys.executable, "-m", "build"])
    wheels = sorted((ROOT / "dist").glob("*.whl"))
    wheel = wheels[-1] if wheels else None
    install_results: list[dict] = []
    package_dir = evidence / "packages"
    package_dir.mkdir(parents=True, exist_ok=True)
    if wheel:
        (package_dir / "wheel.sha256").write_text(f"{sha256(wheel)}  {wheel.name}\n", encoding="utf-8")
        with tempfile.TemporaryDirectory(prefix="sliderefine-wheel-smoke-") as tmp:
            venv = Path(tmp) / "venv"
            install_results.append(run([sys.executable, "-m", "venv", str(venv)]))
            python = venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
            install_results.append(run([str(python), "-m", "pip", "install", str(wheel)]))
            install_results.append(run([str(python), "-m", "sliderefine.cli.main", "doctor", "--json"]))
            install_results.append(run([str(python), "-m", "sliderefine.cli.main", "capabilities", "--json"]))
    web_files = sorted((ROOT / "src" / "sliderefine" / "server" / "web").glob("*"))
    web_digest = hashlib.sha256()
    for path in web_files:
        web_digest.update(path.name.encode("utf-8"))
        web_digest.update(path.read_bytes())
    (package_dir / "web-bundle.sha256").write_text(f"{web_digest.hexdigest()}  sliderefine/server/web\n", encoding="utf-8")
    report = {
        "status": (
            "ok"
            if build_result["status"] == "ok"
            and wheel is not None
            and all(result["status"] == "ok" for result in install_results)
            else "fail"
        ),
        "build": build_result,
        "wheel": str(wheel) if wheel else None,
        "installSmoke": install_results,
    }
    write_json(package_dir / "report.json", report)
    return report


def supply_chain_report(evidence: Path) -> dict:
    supply_dir = evidence / "supply-chain"
    supply_dir.mkdir(parents=True, exist_ok=True)
    license_files = [ROOT / "LICENSE", ROOT / "NOTICE"]
    python_audit_path = supply_dir / "python-audit.json"
    sbom_path = supply_dir / "sbom.cyclonedx.json"
    npm_audit_path = supply_dir / "npm-audit.json"
    python_audit = run(
        [
            sys.executable,
            "-m",
            "pip_audit",
            ".",
            "--format",
            "json",
            "--output",
            str(python_audit_path),
            "--progress-spinner",
            "off",
        ]
    )
    python_sbom = run(
        [
            sys.executable,
            "-m",
            "pip_audit",
            ".",
            "--format",
            "cyclonedx-json",
            "--output",
            str(sbom_path),
            "--progress-spinner",
            "off",
        ]
    )
    npm_audit = run([npm_command(), "audit", "--omit=dev", "--audit-level=high", "--json"], cwd=ROOT / "apps" / "web")
    if npm_audit["stdout"]:
        try:
            write_json(npm_audit_path, json.loads(npm_audit["stdout"]))
        except json.JSONDecodeError:
            npm_audit_path.write_text(npm_audit["stdout"], encoding="utf-8")
    else:
        write_json(npm_audit_path, {"status": "error", "message": "npm audit produced no JSON"})
    missing_license_files = [path.name for path in license_files if not path.exists() or path.stat().st_size == 0]
    report = {
        "status": (
            "ok"
            if not missing_license_files
            and python_audit["status"] == "ok"
            and python_sbom["status"] == "ok"
            and npm_audit["status"] == "ok"
            and python_audit_path.exists()
            and sbom_path.exists()
            and npm_audit_path.exists()
            else "fail"
        ),
        "licensePolicy": {
            "licenseFile": "LICENSE",
            "noticeFile": "NOTICE",
            "missing": missing_license_files,
        },
        "pythonAudit": python_audit,
        "pythonSbom": python_sbom,
        "npmAudit": npm_audit,
    }
    write_json(supply_dir / "report.json", report)
    return report


def browser_reports(evidence: Path) -> dict:
    npm = npm_command()
    contract = run([npm, "test"], cwd=ROOT / "apps" / "web")
    browser_env = {**os_environ(), "SLIDEREFINE_EVIDENCE_DIR": str(evidence)}
    browser_report_path = evidence / "browser" / "report.json"
    e2e = run([npm, "run", "test:e2e"], cwd=ROOT / "apps" / "web", env=browser_env)
    static_payload = {}
    if browser_report_path.exists():
        static_payload = json.loads(browser_report_path.read_text(encoding="utf-8"))
        write_json(evidence / "browser" / "static-report.json", static_payload)
    local_service = run(
        [
            sys.executable,
            "-m",
            "tools.local_browser_e2e",
            "tests/fixtures/shape-heavy.png",
            "--evidence",
            str(evidence.relative_to(ROOT)),
        ],
        env=browser_env,
    )
    contract_report = {"status": contract["status"], "command": contract}
    write_json(evidence / "contract" / "report.json", contract_report)
    local_payload = {}
    if browser_report_path.exists():
        local_payload = json.loads(browser_report_path.read_text(encoding="utf-8"))
    if static_payload or local_payload:
        payload = {
            "status": (
                "ok"
                if contract["status"] == "ok" and e2e["status"] == "ok" and local_service["status"] == "ok"
                else "fail"
            ),
            "staticBrowser": static_payload,
            "localServiceBrowser": local_payload,
            "transcripts": {
                "static": static_payload.get("transcript", []),
                "localService": local_payload.get("transcript", []),
            },
        }
        payload["contractCommand"] = contract
        payload["browserCommand"] = e2e
        payload["localServiceCommand"] = local_service
        write_json(browser_report_path, payload)
        return payload
    report = {
        "status": "fail",
        "contractCommand": contract,
        "browserCommand": e2e,
        "localServiceCommand": local_service,
    }
    write_json(browser_report_path, report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", default="evidence/working")
    args = parser.parse_args()
    evidence = (ROOT / args.evidence).resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    write_environment(evidence)
    write_source_tree(evidence)
    write_hashes(evidence)

    unit = run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/unit",
            "tests/contract",
            "tests/integration",
            "tests/mcp",
            "tests/security",
            f"--junitxml={evidence / 'unit' / 'junit.xml'}",
            "-q",
        ]
    )
    typecheck = run([sys.executable, "-m", "mypy", "src"])
    write_json(evidence / "typecheck" / "report.json", {"status": typecheck["status"], "command": typecheck})
    write_json(evidence / "unit" / "report.json", {"status": unit["status"], "command": unit})
    cli_e2e(evidence)
    golden(evidence)
    visual(evidence)
    browser_reports(evidence)
    mcp_report(evidence)
    security_report(evidence)
    package_reports(evidence)
    supply_chain_report(evidence)
    benchmark = run(
        [
            sys.executable,
            "-m",
            "sliderefine.cli.main",
            "benchmark",
            "tests/fixtures/manifest.json",
            "--output",
            str(evidence / "performance" / "benchmark.json"),
            "--json",
        ]
    )
    write_json(evidence / "performance" / "command.json", {"status": benchmark["status"], "command": benchmark})
    verdict = run(
        [
            sys.executable,
            "-m",
            "tools.release_gate",
            "--evidence",
            str(evidence),
            "--output",
            str(evidence / "release-verdict.json"),
        ]
    )
    verdict_path = evidence / "release-verdict.json"
    if verdict_path.exists():
        print(verdict_path.read_text(encoding="utf-8"))
    if verdict["exitCode"] != 0:
        for report_path in sorted(evidence.glob("*/*.json")):
            try:
                payload = json.loads(report_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            status = payload.get("status") or payload.get("overall")
            if status in {"fail", "error"}:
                print(f"{report_path.relative_to(evidence)}: {json.dumps(payload, ensure_ascii=False)[:4000]}")
    return verdict["exitCode"]


if __name__ == "__main__":
    raise SystemExit(main())
