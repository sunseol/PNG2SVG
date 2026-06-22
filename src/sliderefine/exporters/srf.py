from __future__ import annotations

import io
import json
import zipfile
import copy
from pathlib import Path
from typing import Any

from sliderefine.domain.document import ValidationError, migrate_document, sha256_bytes, validate_document
from sliderefine.version import __version__

MIME_TYPE = "application/vnd.sliderefine+zip"
ZIP_TIMESTAMP = (2026, 1, 1, 0, 0, 0)
MAX_ENTRIES = 2048
MAX_UNCOMPRESSED_BYTES = 512 * 1024 * 1024
MAX_COMPRESSION_RATIO = 200


def _json_bytes(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, ZIP_TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    return info


def _safe_entry(name: str) -> bool:
    return bool(name) and not name.startswith("/") and "\\" not in name and ".." not in Path(name).parts


def save_document(document: dict[str, Any], output_path: Path) -> dict[str, Any]:
    document = migrate_document(document)
    errors = validate_document(document)
    if errors:
        raise ValidationError(f"Cannot save invalid document: {errors[:3]}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    stored_document = copy.deepcopy(document)
    for asset in stored_document.get("assets", {}).values():
        for key in list(asset):
            if key.startswith("_"):
                del asset[key]
    document_bytes = _json_bytes(stored_document)
    manifest_assets: dict[str, Any] = {}
    entries: dict[str, bytes] = {
        "mimetype": MIME_TYPE.encode("ascii"),
        "document.json": document_bytes,
    }

    for asset_id, asset in sorted(document.get("assets", {}).items()):
        path = asset["path"]
        data = asset.get("_bytes")
        if data is None:
            source_path = asset.get("_sourcePath")
            if source_path:
                data = Path(source_path).read_bytes()
            else:
                continue
        if not _safe_entry(path):
            raise ValidationError(f"Unsafe asset path: {path}")
        byte_data = bytes(data)
        entries[path] = byte_data
        digest = sha256_bytes(byte_data)
        manifest_assets[asset_id] = {
            "path": path,
            "mimeType": asset["mimeType"],
            "byteLength": len(byte_data),
            "sha256": digest,
        }

    manifest = {
        "format": {
            "name": "slide-refine-document",
            "version": "1.0.0",
            "minimumReaderVersion": "1.0.0",
        },
        "generator": {"name": "sliderefine", "version": __version__},
        "documentPath": "document.json",
        "documentSha256": sha256_bytes(document_bytes),
        "assets": manifest_assets,
    }
    entries["manifest.json"] = _json_bytes(manifest)

    with zipfile.ZipFile(output_path, "w") as archive:
        for name in sorted(entries):
            archive.writestr(_zip_info(name), entries[name])
    return manifest


def _check_archive(path: Path) -> None:
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        if len(infos) > MAX_ENTRIES:
            raise ValidationError("Archive has too many entries")
        total_uncompressed = sum(info.file_size for info in infos)
        total_compressed = max(1, sum(info.compress_size for info in infos))
        if total_uncompressed > MAX_UNCOMPRESSED_BYTES:
            raise ValidationError("Archive exceeds uncompressed size limit")
        if total_uncompressed / total_compressed > MAX_COMPRESSION_RATIO:
            raise ValidationError("Archive compression ratio exceeds limit")
        for info in infos:
            if not _safe_entry(info.filename):
                raise ValidationError(f"Unsafe ZIP entry: {info.filename}")


def load_document(path: Path) -> dict[str, Any]:
    try:
        _check_archive(path)
        with zipfile.ZipFile(path) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            if not isinstance(manifest, dict):
                raise ValidationError("manifest.json must be an object")
            document_path = manifest.get("documentPath", "document.json")
            if not isinstance(document_path, str) or not _safe_entry(document_path):
                raise ValidationError("Unsafe document path")
            document_bytes = archive.read(document_path)
            if sha256_bytes(document_bytes) != manifest.get("documentSha256"):
                raise ValidationError("document.json digest mismatch")
            document = json.loads(document_bytes)
            if not isinstance(document, dict):
                raise ValidationError("document.json must be an object")
            document = migrate_document(document)
            for asset_id, asset in document.get("assets", {}).items():
                manifest_asset = manifest.get("assets", {}).get(asset_id)
                if not manifest_asset:
                    raise ValidationError(f"Asset missing from manifest: {asset_id}")
                asset_path = manifest_asset["path"]
                if not isinstance(asset_path, str) or not _safe_entry(asset_path):
                    raise ValidationError(f"Unsafe asset path: {asset_path}")
                data = archive.read(asset_path)
                if sha256_bytes(data) != manifest_asset["sha256"]:
                    raise ValidationError(f"Asset digest mismatch: {asset_id}")
                asset["_bytes"] = data
    except (json.JSONDecodeError, KeyError, zipfile.BadZipFile) as exc:
        raise ValidationError(f"Malformed SRF archive: {exc}") from exc
    errors = validate_document(document)
    if errors:
        raise ValidationError(f"Invalid document: {errors[:3]}")
    return document


def read_asset_bytes(document: dict[str, Any], asset_id: str, archive_path: Path | None = None) -> bytes:
    asset = document["assets"][asset_id]
    if "_bytes" in asset:
        return bytes(asset["_bytes"])
    if archive_path is None:
        raise ValidationError(f"Asset bytes unavailable: {asset_id}")
    with zipfile.ZipFile(archive_path) as archive:
        return archive.read(asset["path"])


def document_to_bytes(document: dict[str, Any]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        document_bytes = _json_bytes(document)
        archive.writestr(_zip_info("document.json"), document_bytes)
    return buffer.getvalue()
