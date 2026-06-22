from __future__ import annotations

import json
from pathlib import Path

from sliderefine.cli.main import main


def test_cli_roundtrip_contract(fixture_image, tmp_path, capsys):
    srf = tmp_path / "fixture.srf"
    assert main(["convert", str(fixture_image), "-o", str(srf), "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["apiVersion"] == "sliderefine.cli/v1"
    assert result["status"] == "ok"
    assert result["documentUri"].startswith("file:")

    assert main(["validate", str(srf), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "ok"

    assert main(["inspect", str(srf), "--json"]) == 0
    inspected = json.loads(capsys.readouterr().out)
    assert inspected["nodeCount"] > 0

    assert main(["query", str(srf), "--type", "image", "--limit", "1", "--json"]) == 0
    queried = json.loads(capsys.readouterr().out)
    node_id = queried["nodes"][0]["id"]
    ops = tmp_path / "ops.json"
    ops.write_text(
        json.dumps(
            {
                "schemaVersion": "1.0.0",
                "operationId": "op-cli-contract",
                "expectedRevision": inspected["revision"],
                "operations": [{"type": "translate", "nodeIds": [node_id], "dx": 2, "dy": 2}],
            }
        ),
        encoding="utf-8",
    )
    edited = tmp_path / "edited.srf"
    assert main(["apply", str(srf), "--operations", str(ops), "-o", str(edited), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["revision"] == inspected["revision"] + 1

    preview = tmp_path / "preview.png"
    assert main(["render", str(edited), "-o", str(preview), "--json"]) == 0
    assert preview.exists() and preview.stat().st_size > 0

    exported = tmp_path / "result.svg"
    assert main(["export", str(edited), "--format", "svg", "-o", str(exported), "--json"]) == 0
    assert exported.exists() and "<svg" in exported.read_text(encoding="utf-8")


def test_cli_convert_source_size_limit(fixture_image, tmp_path, capsys):
    output = tmp_path / "too-large.srf"
    exit_code = main(
        [
            "convert",
            str(fixture_image),
            "-o",
            str(output),
            "--max-source-bytes",
            "1",
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 70
    assert payload["status"] == "error"
    assert payload["code"] == "VALUEERROR"
    assert "limit is 1" in payload["message"]


def test_cli_convert_timeout_limit(fixture_image, tmp_path, capsys):
    output = tmp_path / "timeout.srf"
    exit_code = main(
        [
            "convert",
            str(fixture_image),
            "-o",
            str(output),
            "--timeout-ms",
            "0",
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 7
    assert payload["status"] == "error"
    assert payload["code"] == "CONVERSION_TIMEOUT"
    assert not output.exists()


def test_local_http_openapi_contract():
    spec = json.loads(Path("schema/openapi.json").read_text(encoding="utf-8"))
    assert spec["openapi"].startswith("3.1.")
    assert "SlideRefineToken" in spec["components"]["securitySchemes"]
    assert spec["security"] == [{"SlideRefineToken": []}]
    expected_paths = {
        "/api/v1/capabilities",
        "/api/v1/documents/current",
        "/api/v1/documents/current/operations",
        "/api/v1/documents/current/render",
        "/api/v1/documents/current/export",
        "/api/v1/documents/current/validate",
    }
    assert expected_paths <= set(spec["paths"])


def test_cli_benchmark_reports_performance_dimensions(tmp_path, capsys):
    output = tmp_path / "benchmark.json"
    assert main(["benchmark", "tests/fixtures/manifest.json", "--output", str(output), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    report = payload["report"]

    assert output.exists()
    assert report["status"] == "ok"
    assert report["summary"]["violationCount"] == 0
    assert report["violations"] == []
    assert report["summary"]["p95DurationMs"] >= 0
    assert report["summary"]["maxPackageSizeBytes"] > 0
    assert report["summary"]["maxPathCommandCount"] >= 0
    assert all(case["packageSizeBytes"] > 0 for case in report["cases"])
    assert all("peakRssBytes" in case for case in report["cases"])
    assert all("pathControlPointCount" in case for case in report["cases"])
    assert all(case["expected"]["conversion"] == "success" for case in report["cases"])
    assert all(case["violations"] == [] for case in report["cases"])


def test_cli_benchmark_enforces_manifest_limits(fixture_image, tmp_path, capsys):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "name": "too-strict",
                        "input": str(fixture_image),
                        "expected": {
                            "conversion": "success",
                            "maximumNodes": 1,
                            "maximumPathNodes": 1,
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    assert main(["benchmark", str(manifest), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    report = payload["report"]

    assert report["status"] == "fail"
    assert report["summary"]["violationCount"] > 0
    assert any(violation["code"] == "MAXIMUM_NODES_EXCEEDED" for violation in report["violations"])
