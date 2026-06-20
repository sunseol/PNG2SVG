from __future__ import annotations

import json
import mimetypes
import secrets
import tempfile
import threading
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

from sliderefine.application.conversion_service import ConversionOptions, convert
from sliderefine.application.operation_service import apply_transaction
from sliderefine.application.validation_service import validation_report
from sliderefine.exporters.png import render_png
from sliderefine.exporters.srf import load_document, save_document
from sliderefine.exporters.svg import export_svg

CSP_HEADER = (
    "default-src 'none'; "
    "script-src 'self'; "
    "style-src 'self'; "
    "connect-src 'self'; "
    "img-src 'self' data: blob:; "
    "base-uri 'none'; "
    "object-src 'none'; "
    "frame-ancestors 'none'"
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _web_root() -> Path:
    repo_web = _repo_root() / "apps" / "web" / "src"
    if repo_web.exists():
        return repo_web
    return Path(__file__).resolve().parent / "web"


def public_document(document: dict[str, Any]) -> dict[str, Any]:
    clean = json.loads(json.dumps(document, default=lambda value: None))
    for asset in clean.get("assets", {}).values():
        for key in list(asset):
            if key.startswith("_"):
                del asset[key]
    return clean


def _restore_private_asset_fields(incoming: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    current_assets = current.get("assets", {})
    for asset_id, asset in incoming.get("assets", {}).items():
        current_asset = current_assets.get(asset_id, {})
        for key in ("_bytes", "_sourcePath"):
            if key in current_asset and key not in asset:
                asset[key] = current_asset[key]
    return incoming


def _json_response(handler: BaseHTTPRequestHandler, payload: dict[str, Any], status: int = 200) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    _security_headers(handler)
    handler.send_header("content-type", "application/json; charset=utf-8")
    handler.send_header("content-length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _security_headers(handler: BaseHTTPRequestHandler) -> None:
    handler.send_header("content-security-policy", CSP_HEADER)
    handler.send_header("x-content-type-options", "nosniff")
    handler.send_header("referrer-policy", "no-referrer")
    handler.send_header("cache-control", "no-store")


def _read_json(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("content-length", "0"))
    return json.loads(handler.rfile.read(length).decode("utf-8") or "{}")


@dataclass
class LocalEditorSession:
    document: dict[str, Any]
    document_path: Path
    root: Path
    token: str
    source_path: Path | None = None

    @classmethod
    def from_input(cls, input_path: Path, root: Path | None = None) -> "LocalEditorSession":
        root = root or Path(tempfile.mkdtemp(prefix="sliderefine-editor-"))
        root.mkdir(parents=True, exist_ok=True)
        input_path = input_path.expanduser().resolve()
        if input_path.suffix.lower() == ".srf":
            document_path = input_path
            document = load_document(document_path)
        else:
            result = convert(input_path, ConversionOptions(ocr_engine="none", skip_text_detection=True))
            document = result.document
            document_path = root / "editor.srf"
            save_document(document, document_path)
        return cls(
            document=document,
            document_path=document_path,
            root=root,
            token=secrets.token_urlsafe(24),
            source_path=input_path,
        )

    def save(self) -> None:
        save_document(self.document, self.document_path)

    def render(self, slide_id: str | None = None) -> Path:
        output = self.root / "preview.png"
        render_png(self.document, output, slide_id=slide_id, archive_path=self.document_path)
        return output

    def export(self, fmt: str = "svg", slide_id: str | None = None) -> Path:
        output = self.root / f"export.{fmt}"
        if fmt == "svg":
            export_svg(self.document, output, slide_id=slide_id, archive_path=self.document_path)
        elif fmt == "png":
            render_png(self.document, output, slide_id=slide_id, archive_path=self.document_path)
        else:
            raise ValueError(f"Unsupported export format: {fmt}")
        return output


class LocalEditorServer:
    def __init__(self, session: LocalEditorSession, host: str = "127.0.0.1", port: int = 0) -> None:
        self.session = session
        self.host = host
        self.port = port
        self.httpd: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        if self.httpd is None:
            raise RuntimeError("Server is not running")
        address = self.httpd.server_address
        host = address[0].decode("utf-8") if isinstance(address[0], bytes) else str(address[0])
        port = int(address[1])
        return f"http://{host}:{port}/index.html?token={self.session.token}"

    def start(self) -> "LocalEditorServer":
        session = self.session

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                return

            def _authorized(self) -> bool:
                parsed = urlparse(self.path)
                token = parse_qs(parsed.query).get("token", [None])[0] or self.headers.get("x-sliderefine-token")
                return token == session.token

            def _origin_allowed(self) -> bool:
                origin = self.headers.get("Origin")
                if not origin:
                    return True
                host = self.headers.get("Host")
                if not host:
                    return False
                return origin == f"http://{host}"

            def _reject_if_unauthorized(self) -> bool:
                if self.path.startswith("/api/"):
                    if not self._origin_allowed():
                        _json_response(self, {"status": "error", "code": "ORIGIN_DENIED"}, HTTPStatus.FORBIDDEN)
                        return True
                    if not self._authorized():
                        _json_response(self, {"status": "error", "code": "UNAUTHORIZED"}, HTTPStatus.UNAUTHORIZED)
                        return True
                return False

            def do_GET(self) -> None:
                if self._reject_if_unauthorized():
                    return
                parsed = urlparse(self.path)
                if parsed.path == "/api/v1/documents/current":
                    _json_response(self, public_document(session.document))
                    return
                if parsed.path == "/api/v1/capabilities":
                    _json_response(
                        self,
                        {
                            "status": "ok",
                            "operations": [
                                "set_text",
                                "translate",
                                "resize",
                                "rotate",
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
                            ],
                            "revision": session.document["revision"],
                        },
                    )
                    return
                self._serve_static(parsed.path)

            def do_PUT(self) -> None:
                if self._reject_if_unauthorized():
                    return
                if urlparse(self.path).path != "/api/v1/documents/current":
                    _json_response(self, {"status": "error", "code": "NOT_FOUND"}, HTTPStatus.NOT_FOUND)
                    return
                session.document = _restore_private_asset_fields(_read_json(self), session.document)
                session.save()
                _json_response(self, {"status": "ok", "documentUri": session.document_path.as_uri(), "revision": session.document["revision"]})

            def do_POST(self) -> None:
                if self._reject_if_unauthorized():
                    return
                parsed = urlparse(self.path)
                try:
                    if parsed.path == "/api/v1/documents/current/operations":
                        transaction = _read_json(self)
                        session.document, result = apply_transaction(session.document, transaction)
                        session.save()
                        _json_response(self, {"status": "ok", "result": result, "document": public_document(session.document)})
                        return
                    if parsed.path == "/api/v1/documents/current/render":
                        payload = _read_json(self)
                        output = session.render(payload.get("slideId"))
                        _json_response(self, {"status": "ok", "previewUri": output.as_uri(), "byteLength": output.stat().st_size})
                        return
                    if parsed.path == "/api/v1/documents/current/export":
                        payload = _read_json(self)
                        output = session.export(payload.get("format", "svg"), payload.get("slideId"))
                        _json_response(self, {"status": "ok", "artifactUri": output.as_uri(), "byteLength": output.stat().st_size})
                        return
                    if parsed.path == "/api/v1/documents/current/validate":
                        _json_response(self, validation_report(session.document))
                        return
                except Exception as exc:
                    _json_response(self, {"status": "error", "code": exc.__class__.__name__, "message": str(exc)}, HTTPStatus.BAD_REQUEST)
                    return
                _json_response(self, {"status": "error", "code": "NOT_FOUND"}, HTTPStatus.NOT_FOUND)

            def _serve_static(self, request_path: str) -> None:
                relative = "index.html" if request_path in {"", "/"} else request_path.lstrip("/")
                path = (_web_root() / relative).resolve()
                root = _web_root().resolve()
                if not str(path).startswith(str(root)) or not path.exists() or not path.is_file():
                    self.send_response(HTTPStatus.NOT_FOUND)
                    _security_headers(self)
                    self.end_headers()
                    return
                data = path.read_bytes()
                self.send_response(HTTPStatus.OK)
                _security_headers(self)
                self.send_header("content-type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                try:
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionAbortedError):
                    return

        self.httpd = ThreadingHTTPServer((self.host, self.port), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        return self

    def stop(self) -> None:
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()
        if self.thread is not None:
            self.thread.join(timeout=5)


def _request_json(url: str, token: str, payload: dict[str, Any] | None = None, method: str = "GET") -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(url, data=data, method=method, headers={"x-sliderefine-token": token, "content-type": "application/json"})
    try:
        with urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise RuntimeError(exc.read().decode("utf-8")) from exc


def run_headless_e2e(input_path: Path) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="sliderefine-editor-") as tmp:
        session = LocalEditorSession.from_input(input_path, Path(tmp))
        server = LocalEditorServer(session).start()
        try:
            base = server.url.split("/index.html", 1)[0]
            loaded = _request_json(f"{base}/api/v1/documents/current", session.token)
            editable_nodes = [
                node for node in loaded["nodes"].values() if node["type"] in {"text", "image", "path", "rect", "ellipse"}
            ]
            operations: list[dict[str, Any]] = []
            if editable_nodes:
                operations.append({"type": "translate", "nodeIds": [editable_nodes[0]["id"]], "dx": 1, "dy": 1})
            text_nodes = [node for node in loaded["nodes"].values() if node["type"] == "text"]
            if text_nodes:
                operations.append({"type": "set_text", "nodeId": text_nodes[0]["id"], "text": "Edited title"})
            transaction = {
                "schemaVersion": "1.0.0",
                "operationId": "op-headless-editor-e2e",
                "expectedRevision": loaded["revision"],
                "dryRun": False,
                "operations": operations,
            }
            applied = _request_json(f"{base}/api/v1/documents/current/operations", session.token, transaction, "POST")
            rendered = _request_json(f"{base}/api/v1/documents/current/render", session.token, {"slideId": next(iter(session.document["slides"]))}, "POST")
            exported = _request_json(f"{base}/api/v1/documents/current/export", session.token, {"format": "svg"}, "POST")
            validated = _request_json(f"{base}/api/v1/documents/current/validate", session.token, {}, "POST")
            return {
                "apiVersion": "sliderefine.cli/v1",
                "status": "ok",
                "command": "edit",
                "journey": "local-editor-api-e2e",
                "url": server.url,
                "apply": applied["result"],
                "render": rendered,
                "export": exported,
                "validate": validated,
                "documentUri": session.document_path.as_uri(),
                "transcript": json.dumps(
                    {"open": True, "apply": applied["result"], "render": rendered, "export": exported, "validate": validated},
                    sort_keys=True,
                ),
            }
        finally:
            server.stop()
