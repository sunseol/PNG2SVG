from __future__ import annotations

import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

from sliderefine.server.local import LocalEditorServer, LocalEditorSession


def request_json(url: str, token: str, payload: dict | None = None, method: str = "GET") -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        url,
        data=data,
        method=method,
        headers={"content-type": "application/json", "x-sliderefine-token": token},
    )
    with urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def request_status(url: str, headers: dict[str, str]) -> int:
    request = Request(url, headers=headers)
    try:
        with urlopen(request, timeout=10) as response:
            return response.status
    except HTTPError as exc:
        return exc.code


def request_upload_json(url: str, token: str, path: Path, filename: str | None = None) -> dict:
    request = Request(
        url,
        data=path.read_bytes(),
        method="POST",
        headers={
            "content-type": "application/octet-stream",
            "x-sliderefine-token": token,
            "x-sliderefine-filename": quote(filename or path.name),
        },
    )
    with urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def response_headers(url: str, headers: dict[str, str] | None = None) -> dict[str, str]:
    request = Request(url, headers=headers or {})
    with urlopen(request, timeout=10) as response:
        return {key.lower(): value for key, value in response.headers.items()}


def test_local_editor_api_roundtrip(fixture_image: Path, tmp_path: Path):
    session = LocalEditorSession.from_input(fixture_image, tmp_path)
    server = LocalEditorServer(session).start()
    try:
        base = server.url.split("/index.html", 1)[0]
        document = request_json(f"{base}/api/v1/documents/current", session.token)
        assert document["revision"] == 1
        node_id = next(iter(document["nodes"]))
        applied = request_json(
            f"{base}/api/v1/documents/current/operations",
            session.token,
            {
                "schemaVersion": "1.0.0",
                "operationId": "op-local-server-test",
                "expectedRevision": document["revision"],
                "operations": [{"type": "translate", "nodeIds": [node_id], "dx": 3, "dy": 4}],
            },
            "POST",
        )
        assert applied["result"]["revision"] == 2
        assert applied["document"]["nodes"][node_id]["transform"][4] == document["nodes"][node_id]["transform"][4] + 3

        rendered = request_json(f"{base}/api/v1/documents/current/render", session.token, {}, "POST")
        exported = request_json(
            f"{base}/api/v1/documents/current/export",
            session.token,
            {"format": "svg"},
            "POST",
        )
        validated = request_json(f"{base}/api/v1/documents/current/validate", session.token, {}, "POST")
        saved = request_json(f"{base}/api/v1/documents/current", session.token, applied["document"], "PUT")
        assert rendered["byteLength"] > 0
        assert exported["byteLength"] > 0
        assert validated["status"] == "ok"
        assert saved["revision"] == 2
    finally:
        server.stop()


def test_local_editor_put_preserves_asset_bytes_for_export(fixture_image: Path, tmp_path: Path):
    session = LocalEditorSession.from_input(fixture_image, tmp_path)
    server = LocalEditorServer(session).start()
    try:
        base = server.url.split("/index.html", 1)[0]
        document = request_json(f"{base}/api/v1/documents/current", session.token)
        response = request_json(f"{base}/api/v1/documents/current", session.token, document, "PUT")
        assert response["status"] == "ok"

        exported = request_json(
            f"{base}/api/v1/documents/current/export",
            session.token,
            {"format": "svg"},
            "POST",
        )
        assert exported["status"] == "ok"
        assert exported["byteLength"] > 0
    finally:
        server.stop()


def test_local_editor_import_upload_replaces_current_document(fixture_image: Path, tmp_path: Path):
    session = LocalEditorSession.from_input(fixture_image, tmp_path)
    server = LocalEditorServer(session).start()
    try:
        base = server.url.split("/index.html", 1)[0]
        imported = request_upload_json(
            f"{base}/api/v1/documents/current/import",
            session.token,
            fixture_image,
            "fresh-upload.png",
        )
        assert imported["status"] == "ok"
        assert imported["revision"] == 1
        assert imported["document"]["assets"]
        assert imported["sourceName"].endswith("fresh-upload.png")

        exported = request_json(
            f"{base}/api/v1/documents/current/export",
            session.token,
            {"format": "svg"},
            "POST",
        )
        assert exported["status"] == "ok"
        assert exported["byteLength"] > 0
    finally:
        server.stop()


def test_local_editor_import_rejects_unsupported_upload(fixture_image: Path, tmp_path: Path):
    session = LocalEditorSession.from_input(fixture_image, tmp_path)
    server = LocalEditorServer(session).start()
    try:
        base = server.url.split("/index.html", 1)[0]
        request = Request(
            f"{base}/api/v1/documents/current/import",
            data=b"not a supported source",
            method="POST",
            headers={
                "content-type": "application/octet-stream",
                "x-sliderefine-token": session.token,
                "x-sliderefine-filename": "notes.txt",
            },
        )
        try:
            with urlopen(request, timeout=10):
                raise AssertionError("Unsupported upload should be rejected")
        except HTTPError as exc:
            body = json.loads(exc.read().decode("utf-8"))
            assert exc.code == 400
            assert body["status"] == "error"
    finally:
        server.stop()


def test_local_editor_sets_csp_and_security_headers(fixture_image: Path, tmp_path: Path):
    session = LocalEditorSession.from_input(fixture_image, tmp_path)
    server = LocalEditorServer(session).start()
    try:
        base = server.url.split("/index.html", 1)[0]
        static_headers = response_headers(server.url)
        api_headers = response_headers(
            f"{base}/api/v1/documents/current",
            {"x-sliderefine-token": session.token},
        )
        for headers in (static_headers, api_headers):
            csp = headers["content-security-policy"]
            assert "default-src 'none'" in csp
            assert "script-src 'self'" in csp
            assert "object-src 'none'" in csp
            assert "frame-ancestors 'none'" in csp
            assert headers["x-content-type-options"] == "nosniff"
            assert headers["referrer-policy"] == "no-referrer"
            assert headers["cache-control"] == "no-store"
    finally:
        server.stop()


def test_local_editor_rejects_missing_token_and_foreign_origin(fixture_image: Path, tmp_path: Path):
    session = LocalEditorSession.from_input(fixture_image, tmp_path)
    server = LocalEditorServer(session).start()
    try:
        base = server.url.split("/index.html", 1)[0]
        document_url = f"{base}/api/v1/documents/current"
        assert request_status(document_url, {}) == 401
        assert request_status(document_url, {"x-sliderefine-token": session.token, "Origin": "http://evil.example"}) == 403
        host = base.removeprefix("http://")
        assert request_status(document_url, {"x-sliderefine-token": session.token, "Origin": f"http://{host}"}) == 200
    finally:
        server.stop()
