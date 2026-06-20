from __future__ import annotations

import argparse
import ctypes
import importlib
import json
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from sliderefine.application.conversion_service import ConversionOptions, ConversionTimeout, convert
from sliderefine.application.operation_service import OperationError, apply_transaction
from sliderefine.application.validation_service import validation_report
from sliderefine.domain.document import path_command_count, path_control_point_count
from sliderefine.exporters.png import render_png
from sliderefine.exporters.srf import load_document, save_document
from sliderefine.exporters.svg import export_svg
from sliderefine.version import __version__

API_VERSION = "sliderefine.cli/v1"


class CliError(Exception):
    def __init__(self, code: str, message: str, exit_code: int = 70) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.exit_code = exit_code


def _print_result(result: dict[str, Any], json_output: bool) -> None:
    if json_output:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    elif result.get("status") == "ok":
        print(result.get("message") or f"{result['command']} ok")


def _ok(command: str, **fields: Any) -> dict[str, Any]:
    return {"apiVersion": API_VERSION, "status": "ok", "command": command, **fields}


def _error(command: str, code: str, message: str) -> dict[str, Any]:
    return {"apiVersion": API_VERSION, "status": "error", "command": command, "code": code, "message": message}


def _uri(path: Path) -> str:
    return path.resolve().as_uri()


def _load_ops(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return {
            "schemaVersion": "1.0.0",
            "operationId": f"op-{int(time.time() * 1000)}",
            "expectedRevision": -1,
            "operations": data,
        }
    return data


def _first_slide(document: dict[str, Any]) -> str:
    return next(iter(document["slides"]))


def cmd_doctor(args: argparse.Namespace) -> dict[str, Any]:
    return _ok(
        "doctor",
        version=__version__,
        python=sys.version.split()[0],
        capabilities=["image", "directory", "pptx", "srf", "svg", "png", "operations", "mcp-stdio"],
    )


def cmd_capabilities(args: argparse.Namespace) -> dict[str, Any]:
    return _ok(
        "capabilities",
        inputFormats=["png", "jpg", "jpeg", "webp", "bmp", "tiff", "pptx", "directory"],
        outputFormats=["srf", "svg", "png"],
        operations=[
            "set_text",
            "translate",
            "resize",
            "rotate",
            "set_transform",
            "set_fill",
            "set_stroke",
            "set_opacity",
            "set_visibility",
            "set_locked",
            "reorder",
            "group",
            "ungroup",
            "duplicate",
            "delete",
            "replace_asset",
            "set_name",
        ],
    )


def cmd_convert(args: argparse.Namespace) -> dict[str, Any]:
    source = Path(args.input)
    output = Path(args.output)
    options = ConversionOptions(
        colors=args.colors,
        ocr_engine=args.ocr_engine,
        deterministic=args.deterministic,
        skip_text_detection=args.skip_text_detection,
        max_slides=args.max_slides,
        max_image_pixels=args.max_image_pixels,
        max_source_bytes=args.max_source_bytes,
        timeout_ms=args.timeout_ms,
    )
    result = convert(source, options)
    if args.format == "srf":
        save_document(result.document, output)
        return _ok(
            "convert",
            documentUri=_uri(output),
            revision=result.document["revision"],
            slides=len(result.document["slides"]),
            warnings=result.warnings,
            metrics=result.metrics,
            message=f"Generated SlideRefine document at {output}",
        )
    if args.format == "svg":
        export_svg(result.document, output)
        return _ok("convert", artifactUri=_uri(output), warnings=result.warnings, metrics=result.metrics)
    if args.format == "png":
        render_png(result.document, output)
        return _ok("convert", artifactUri=_uri(output), warnings=result.warnings, metrics=result.metrics)
    raise CliError("INVALID_FORMAT", f"Unsupported output format: {args.format}", 2)


def _inspect(document: dict[str, Any]) -> dict[str, Any]:
    nodes_by_type: dict[str, int] = {}
    for node in document["nodes"].values():
        nodes_by_type[node["type"]] = nodes_by_type.get(node["type"], 0) + 1
    return {
        "documentId": document["documentId"],
        "revision": document["revision"],
        "slides": list(document["slides"].values()),
        "nodeCount": len(document["nodes"]),
        "nodesByType": nodes_by_type,
        "assetCount": len(document["assets"]),
        "diagnostics": document.get("diagnostics", []),
    }


def cmd_inspect(args: argparse.Namespace) -> dict[str, Any]:
    document = load_document(Path(args.document))
    return _ok("inspect", **_inspect(document))


def cmd_query(args: argparse.Namespace) -> dict[str, Any]:
    document = load_document(Path(args.document))
    nodes = []
    for node in document["nodes"].values():
        if args.type and node.get("type") != args.type:
            continue
        confidence = float(node.get("provenance", {}).get("confidence") or 0)
        if args.confidence_below is not None and confidence >= args.confidence_below:
            continue
        nodes.append(node)
    if args.limit:
        nodes = nodes[: args.limit]
    return _ok("query", revision=document["revision"], nodes=nodes, count=len(nodes))


def cmd_validate(args: argparse.Namespace) -> dict[str, Any]:
    document = load_document(Path(args.document))
    report = validation_report(document)
    return _ok("validate", **report)


def cmd_apply(args: argparse.Namespace) -> dict[str, Any]:
    source_path = Path(args.document)
    output_path = Path(args.output)
    document = load_document(source_path)
    transaction = _load_ops(Path(args.operations))
    if transaction.get("expectedRevision") == -1:
        transaction["expectedRevision"] = document["revision"]
    if args.dry_run:
        transaction["dryRun"] = True
    updated, result = apply_transaction(document, transaction)
    if not transaction.get("dryRun"):
        save_document(updated, output_path)
    return _ok("apply", documentUri=_uri(output_path), **result)


def cmd_render(args: argparse.Namespace) -> dict[str, Any]:
    document_path = Path(args.document)
    document = load_document(document_path)
    render_png(document, Path(args.output), slide_id=args.slide, archive_path=document_path, scale=args.scale)
    return _ok("render", artifactUri=_uri(Path(args.output)), slideId=args.slide or _first_slide(document))


def cmd_export(args: argparse.Namespace) -> dict[str, Any]:
    document_path = Path(args.document)
    document = load_document(document_path)
    output = Path(args.output)
    if args.format == "svg":
        export_svg(document, output, slide_id=args.slide, archive_path=document_path, include_guides=args.include_guides)
    elif args.format == "png":
        render_png(document, output, slide_id=args.slide, archive_path=document_path)
    else:
        raise CliError("INVALID_FORMAT", f"Unsupported export format: {args.format}", 2)
    return _ok("export", artifactUri=_uri(output), format=args.format, slideId=args.slide or _first_slide(document))


class ProcessMemoryCounters(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_ulong),
        ("PageFaultCount", ctypes.c_ulong),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def _process_peak_rss_bytes() -> int | None:
    if sys.platform == "win32":
        windll = getattr(ctypes, "windll", None)
        if windll is None:
            return None
        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        get_current_process = windll.kernel32.GetCurrentProcess
        get_current_process.restype = ctypes.c_void_p
        get_process_memory_info = windll.psapi.GetProcessMemoryInfo
        get_process_memory_info.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ProcessMemoryCounters),
            ctypes.c_ulong,
        ]
        get_process_memory_info.restype = ctypes.c_int
        handle = get_current_process()
        if get_process_memory_info(handle, ctypes.byref(counters), counters.cb):
            return int(counters.PeakWorkingSetSize)
        return None
    try:
        resource = importlib.import_module("resource")
        usage = resource.getrusage(resource.RUSAGE_SELF)
    except Exception:
        return None
    multiplier = 1 if sys.platform == "darwin" else 1024
    return int(usage.ru_maxrss) * multiplier


def _percentile(values: list[int], percentile: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round((percentile / 100.0) * (len(ordered) - 1))))
    return ordered[index]


def _node_type_counts(document: dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for node in document.get("nodes", {}).values():
        node_type = str(node.get("type", "unknown"))
        counts[node_type] = counts.get(node_type, 0) + 1
    return counts


def _limit_violation(expected: dict[str, Any], result: dict[str, Any], key: str, metric: str, code: str) -> dict[str, Any] | None:
    if key not in expected:
        return None
    actual = result.get(metric)
    if actual is not None and int(actual) > int(expected[key]):
        return {"code": code, "metric": metric, "limit": int(expected[key]), "actual": actual}
    return None


def _benchmark_violations(expected: dict[str, Any], result: dict[str, Any]) -> list[dict[str, Any]]:
    violations: list[dict[str, Any]] = []
    if expected.get("conversion", "success") == "error":
        violations.append({"code": "EXPECTED_CONVERSION_ERROR_NOT_RAISED"})
    if "minimumSlides" in expected and int(result.get("slides", 0)) < int(expected["minimumSlides"]):
        violations.append(
            {
                "code": "MINIMUM_SLIDES_NOT_MET",
                "metric": "slides",
                "limit": int(expected["minimumSlides"]),
                "actual": result.get("slides", 0),
            }
        )
    for violation in (
        _limit_violation(expected, result, "maximumNodes", "nodes", "MAXIMUM_NODES_EXCEEDED"),
        _limit_violation(expected, result, "maximumPathNodes", "pathNodes", "MAXIMUM_PATH_NODES_EXCEEDED"),
        _limit_violation(expected, result, "maximumPathCommands", "pathCommandCount", "MAXIMUM_PATH_COMMANDS_EXCEEDED"),
        _limit_violation(
            expected,
            result,
            "maximumPathControlPoints",
            "pathControlPointCount",
            "MAXIMUM_PATH_CONTROL_POINTS_EXCEEDED",
        ),
        _limit_violation(expected, result, "maximumPackageSizeBytes", "packageSizeBytes", "MAXIMUM_PACKAGE_SIZE_EXCEEDED"),
        _limit_violation(expected, result, "maximumDurationMs", "durationMs", "MAXIMUM_DURATION_EXCEEDED"),
    ):
        if violation:
            violations.append(violation)
    if expected.get("mustPreserveRasterRegions") and int(result.get("imageNodes", 0)) <= 0:
        violations.append({"code": "RASTER_REGION_NOT_PRESERVED", "metric": "imageNodes", "actual": result.get("imageNodes", 0)})
    return violations


def cmd_benchmark(args: argparse.Namespace) -> dict[str, Any]:
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    cases = manifest.get("cases", [])
    results = []
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="sliderefine-benchmark-") as tmp:
        package_dir = Path(tmp)
        for index, case in enumerate(cases, start=1):
            case_start = time.perf_counter()
            rss_before = _process_peak_rss_bytes()
            expected = case.get("expected", {})
            try:
                result = convert(Path(case["input"]), ConversionOptions(ocr_engine="none"))
            except Exception as exc:
                case_result = {
                    "name": case.get("name") or case["input"],
                    "input": case["input"],
                    "status": "error",
                    "expected": expected,
                    "errorCode": exc.__class__.__name__.upper(),
                    "errorMessage": str(exc),
                    "durationMs": int((time.perf_counter() - case_start) * 1000),
                }
                case_result["violations"] = (
                    []
                    if expected.get("conversion") == "error"
                    else [{"code": "UNEXPECTED_CONVERSION_ERROR", "errorCode": case_result["errorCode"]}]
                )
                results.append(case_result)
                continue
            package_path = package_dir / f"case-{index:03d}.srf"
            save_document(result.document, package_path)
            rss_after = _process_peak_rss_bytes()
            type_counts = _node_type_counts(result.document)
            case_result = {
                "name": case.get("name") or case["input"],
                "input": case["input"],
                "status": "ok",
                "expected": expected,
                "slides": len(result.document["slides"]),
                "nodes": len(result.document["nodes"]),
                "nodesByType": type_counts,
                "imageNodes": type_counts.get("image", 0),
                "pathNodes": type_counts.get("path", 0),
                "pathCommandCount": path_command_count(result.document),
                "pathControlPointCount": path_control_point_count(result.document),
                "assetCount": len(result.document["assets"]),
                "packageSizeBytes": package_path.stat().st_size,
                "peakRssBytes": rss_after,
                "peakRssDeltaBytes": None if rss_before is None or rss_after is None else max(0, rss_after - rss_before),
                "durationMs": int((time.perf_counter() - case_start) * 1000),
            }
            case_result["violations"] = _benchmark_violations(expected, case_result)
            results.append(case_result)
    durations = [int(case["durationMs"]) for case in results if "durationMs" in case]
    package_sizes = [int(case["packageSizeBytes"]) for case in results if "packageSizeBytes" in case]
    path_counts = [int(case["pathCommandCount"]) for case in results if "pathCommandCount" in case]
    peak_rss_values = [int(case["peakRssBytes"]) for case in results if case.get("peakRssBytes") is not None]
    violations = [violation for case in results for violation in case.get("violations", [])]
    report = {
        "status": "ok" if not violations else "fail",
        "durationMs": int((time.perf_counter() - started) * 1000),
        "summary": {
            "caseCount": len(results),
            "violationCount": len(violations),
            "p95DurationMs": _percentile(durations, 95),
            "maxPeakRssBytes": max(peak_rss_values, default=0),
            "maxPackageSizeBytes": max(package_sizes, default=0),
            "maxPathCommandCount": max(path_counts, default=0),
        },
        "violations": violations,
        "cases": results,
    }
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return _ok("benchmark", report=report)


def cmd_edit(args: argparse.Namespace) -> dict[str, Any]:
    import webbrowser

    from sliderefine.server.local import LocalEditorServer, LocalEditorSession, run_headless_e2e

    if args.headless_e2e:
        return run_headless_e2e(Path(args.input))
    session = LocalEditorSession.from_input(Path(args.input))
    server = LocalEditorServer(session, host=args.host, port=args.port).start()
    if not args.no_browser:
        webbrowser.open(server.url)
    print(f"SlideRefine editor: {server.url}", file=sys.stderr)
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        server.stop()
    return _ok("edit", url=server.url, documentUri=session.document_path.as_uri())


def cmd_serve(args: argparse.Namespace) -> dict[str, Any]:
    from sliderefine.server.local import LocalEditorServer, LocalEditorSession

    session = LocalEditorSession.from_input(Path(args.input))
    server = LocalEditorServer(session, host=args.host, port=args.port).start()
    print(f"SlideRefine service: {server.url}", file=sys.stderr)
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        server.stop()
    return _ok("serve", url=server.url, documentUri=session.document_path.as_uri())


def cmd_mcp(args: argparse.Namespace) -> dict[str, Any]:
    from sliderefine.mcp.server import run_stdio

    if args.transport != "stdio":
        raise CliError("INVALID_TRANSPORT", "Only stdio transport is supported in this slice", 2)
    run_stdio()
    return _ok("mcp")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sliderefine")
    parser.add_argument("--json", action="store_true", help="Emit a machine-readable JSON result.")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_json(p: argparse.ArgumentParser) -> argparse.ArgumentParser:
        p.add_argument("--json", action="store_true", help=argparse.SUPPRESS)
        return p

    doctor = add_json(sub.add_parser("doctor"))
    doctor.set_defaults(func=cmd_doctor)

    capabilities = add_json(sub.add_parser("capabilities"))
    capabilities.set_defaults(func=cmd_capabilities)

    convert_p = add_json(sub.add_parser("convert"))
    convert_p.add_argument("input")
    convert_p.add_argument("-o", "--output", required=True)
    convert_p.add_argument("--format", choices=("srf", "svg", "png"), default="srf")
    convert_p.add_argument("--colors", type=int, default=12)
    convert_p.add_argument("--ocr-engine", choices=("auto", "windows", "none"), default="none")
    convert_p.add_argument("--skip-text-detection", action="store_true")
    convert_p.add_argument("--deterministic", action="store_true")
    convert_p.add_argument("--max-slides", type=int, default=100)
    convert_p.add_argument("--max-image-pixels", type=int, default=100_000_000)
    convert_p.add_argument("--max-source-bytes", type=int, default=256 * 1024 * 1024)
    convert_p.add_argument("--timeout", "--timeout-ms", dest="timeout_ms", type=int)
    convert_p.set_defaults(func=cmd_convert)

    inspect_p = add_json(sub.add_parser("inspect"))
    inspect_p.add_argument("document")
    inspect_p.set_defaults(func=cmd_inspect)

    query_p = add_json(sub.add_parser("query"))
    query_p.add_argument("document")
    query_p.add_argument("--type")
    query_p.add_argument("--confidence-below", type=float)
    query_p.add_argument("--limit", type=int, default=100)
    query_p.set_defaults(func=cmd_query)

    validate_p = add_json(sub.add_parser("validate"))
    validate_p.add_argument("document")
    validate_p.set_defaults(func=cmd_validate)

    apply_p = add_json(sub.add_parser("apply"))
    apply_p.add_argument("document")
    apply_p.add_argument("--operations", required=True)
    apply_p.add_argument("-o", "--output", required=True)
    apply_p.add_argument("--dry-run", action="store_true")
    apply_p.set_defaults(func=cmd_apply)

    render_p = add_json(sub.add_parser("render"))
    render_p.add_argument("document")
    render_p.add_argument("--slide")
    render_p.add_argument("-o", "--output", required=True)
    render_p.add_argument("--scale", type=float, default=1.0)
    render_p.set_defaults(func=cmd_render)

    export_p = add_json(sub.add_parser("export"))
    export_p.add_argument("document")
    export_p.add_argument("--format", choices=("svg", "png"), default="svg")
    export_p.add_argument("--slide")
    export_p.add_argument("-o", "--output", required=True)
    export_p.add_argument("--include-guides", action="store_true")
    export_p.set_defaults(func=cmd_export)

    edit_p = add_json(sub.add_parser("edit"))
    edit_p.add_argument("input")
    edit_p.add_argument("--headless-e2e", action="store_true")
    edit_p.add_argument("--host", default="127.0.0.1")
    edit_p.add_argument("--port", type=int, default=0)
    edit_p.add_argument("--no-browser", action="store_true")
    edit_p.set_defaults(func=cmd_edit)

    serve_p = add_json(sub.add_parser("serve"))
    serve_p.add_argument("input")
    serve_p.add_argument("--host", default="127.0.0.1")
    serve_p.add_argument("--port", type=int, default=0)
    serve_p.set_defaults(func=cmd_serve)

    mcp_p = add_json(sub.add_parser("mcp"))
    mcp_p.add_argument("--transport", choices=("stdio",), default="stdio")
    mcp_p.set_defaults(func=cmd_mcp)

    benchmark_p = add_json(sub.add_parser("benchmark"))
    benchmark_p.add_argument("manifest")
    benchmark_p.add_argument("--output")
    benchmark_p.set_defaults(func=cmd_benchmark)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = args.func(args)
        _print_result(result, args.json)
        return 0
    except CliError as exc:
        _print_result(_error(args.command, exc.code, exc.message), args.json)
        return exc.exit_code
    except OperationError as exc:
        _print_result(_error(args.command, exc.code, exc.message), args.json)
        return 6 if exc.code == "REVISION_CONFLICT" else 5
    except ConversionTimeout as exc:
        _print_result(_error(args.command, "CONVERSION_TIMEOUT", str(exc)), args.json)
        return 7
    except FileNotFoundError as exc:
        _print_result(_error(args.command, "INPUT_NOT_FOUND", str(exc)), args.json)
        return 3
    except Exception as exc:
        _print_result(_error(args.command, exc.__class__.__name__.upper(), str(exc)), args.json)
        return 70


if __name__ == "__main__":
    raise SystemExit(main())
