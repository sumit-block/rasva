"""Asset storage: validated uploads, promoted job outputs and safe file resolution.

Files are stored under generated names (``<asset_id><ext>``); user-supplied names
are kept only as display metadata, so no user string ever becomes a path.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import unicodedata
from pathlib import Path
from typing import Any, BinaryIO

from rasva.config import Paths
from rasva.db import dumps, loads, new_id, utcnow

CHUNK = 1024 * 1024

KIND_DIRS = {"reference": "references", "import": "imports", "output": "outputs"}

# extension -> (media type, magic-byte checker)
ALLOWED_UPLOADS: dict[str, str] = {
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".flac": "audio/flac",
    ".m4a": "audio/mp4",
    ".ogg": "audio/ogg",
    ".webm": "audio/webm",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".txt": "text/plain",
    ".md": "text/markdown",
}


class AssetError(ValueError):
    pass


class AssetNotFound(Exception):
    pass


class QuotaExceeded(Exception):
    def __init__(self, reasons: list[str]) -> None:
        super().__init__("; ".join(reasons))
        self.reasons = reasons


def _magic_ok(ext: str, head: bytes) -> bool:
    if ext == ".wav":
        return head[:4] == b"RIFF" and head[8:12] == b"WAVE"
    if ext == ".mp3":
        return head[:3] == b"ID3" or (len(head) > 1 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0)
    if ext == ".flac":
        return head[:4] == b"fLaC"
    if ext == ".m4a":
        return head[4:8] == b"ftyp"
    if ext == ".ogg":
        return head[:4] == b"OggS"
    if ext == ".webm":
        return head[:4] == b"\x1a\x45\xdf\xa3"
    if ext == ".png":
        return head[:8] == b"\x89PNG\r\n\x1a\n"
    if ext in (".jpg", ".jpeg"):
        return head[:3] == b"\xff\xd8\xff"
    if ext == ".webp":
        return head[:4] == b"RIFF" and head[8:12] == b"WEBP"
    if ext in (".txt", ".md"):
        try:
            head.decode("utf-8")
        except UnicodeDecodeError as exc:
            # A multi-byte character may be cut at the sniff boundary.
            return exc.start >= len(head) - 3
        return b"\x00" not in head
    return False


def display_name(name: str) -> str:
    """Strip directories and control characters; keep Unicode (Hindi names etc.)."""
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    base = "".join(ch for ch in base if unicodedata.category(ch)[0] != "C").strip()
    base = base.lstrip(".")
    return (base or "file")[:200]


def resolve(paths: Paths, rel_path: str) -> Path:
    root = paths.projects.resolve()
    full = (root / rel_path).resolve()
    if not full.is_relative_to(root):
        raise AssetError("Invalid asset path")
    return full


def _row(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["metadata"] = loads(d["metadata"], {})
    d["protected"] = bool(d["protected"])
    return d


def get(conn: sqlite3.Connection, asset_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
    if row is None:
        raise AssetNotFound(asset_id)
    return _row(row)


def list_assets(
    conn: sqlite3.Connection, project_id: str, kind: str | None = None
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM assets WHERE project_id = ?"
    params: list[Any] = [project_id]
    if kind:
        sql += " AND kind = ?"
        params.append(kind)
    sql += " ORDER BY created_at DESC"
    return [_row(r) for r in conn.execute(sql, params)]


def store_upload(
    conn: sqlite3.Connection,
    paths: Paths,
    *,
    project_id: str,
    kind: str,
    original_name: str,
    stream: BinaryIO,
    max_bytes: int,
    preflight: Any,
) -> dict[str, Any]:
    """Stream an upload to a temp file, validate, hash, then atomically move it in.

    ``preflight(n)`` returns a storage Preflight for ``n`` bytes; it is checked
    before writing (with the size limit) and again with the exact size.
    """
    if kind not in ("reference", "import"):
        raise AssetError("Uploads must be references or imports")
    name = display_name(original_name)
    ext = Path(name).suffix.lower()
    media_type = ALLOWED_UPLOADS.get(ext)
    if media_type is None:
        raise AssetError(
            f"Files of type '{ext or 'unknown'}' are not supported. "
            "Use WAV, MP3, FLAC, M4A, OGG, PNG, JPEG, WebP, TXT or MD."
        )

    asset_id = new_id("ast")
    tmp = paths.uploads_tmp / f"{asset_id}.part"
    digest = hashlib.sha256()
    size = 0
    head = b""
    try:
        with tmp.open("wb") as out:
            while chunk := stream.read(CHUNK):
                if len(head) < 64:
                    head += chunk[: 64 - len(head)]
                size += len(chunk)
                if size > max_bytes:
                    raise AssetError(f"File is larger than the {max_bytes // 1024**2} MB limit.")
                digest.update(chunk)
                out.write(chunk)
        if size == 0:
            raise AssetError("The file is empty.")
        if not _magic_ok(ext, head):
            raise AssetError("The file contents do not match its type. It may be damaged.")
        check = preflight(size)
        if not check.allowed:
            raise QuotaExceeded(check.reasons)

        rel = f"{project_id}/{KIND_DIRS[kind]}/{asset_id}{ext}"
        dest = resolve(paths, rel)
        dest.parent.mkdir(parents=True, exist_ok=True)
        os.replace(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)

    now = utcnow()
    try:
        conn.execute(
            "INSERT INTO assets(id, project_id, kind, media_type, original_name, rel_path,"
            " size_bytes, sha256, metadata, protected, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                asset_id,
                project_id,
                kind,
                media_type,
                name,
                rel,
                size,
                digest.hexdigest(),
                dumps({}),
                1 if kind == "reference" else 0,
                now,
            ),
        )
    except BaseException:
        dest.unlink(missing_ok=True)
        raise
    return get(conn, asset_id)


def promote_output(
    conn: sqlite3.Connection,
    paths: Paths,
    *,
    project_id: str,
    job_id: str,
    staging_dir: Path,
    filename: str,
    media_type: str,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    """Move a worker-written file from job staging into the project and record it."""
    if not filename or "/" in filename or "\\" in filename or filename in (".", ".."):
        raise AssetError(f"Engine returned an invalid output name: {filename!r}")
    src = (staging_dir / filename).resolve()
    if not src.is_relative_to(staging_dir.resolve()) or not src.is_file():
        raise AssetError(f"Engine output {filename!r} is missing")

    digest = hashlib.sha256()
    with src.open("rb") as fh:
        while chunk := fh.read(CHUNK):
            digest.update(chunk)
    asset_id = new_id("ast")
    ext = Path(filename).suffix.lower()[:10]
    rel = f"{project_id}/outputs/{asset_id}{ext}"
    dest = resolve(paths, rel)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(src, dest)
    size = dest.stat().st_size
    try:
        conn.execute(
            "INSERT INTO assets(id, project_id, kind, media_type, original_name, rel_path,"
            " size_bytes, sha256, metadata, protected, job_id, created_at)"
            " VALUES (?, ?, 'output', ?, ?, ?, ?, ?, ?, 0, ?, ?)",
            (
                asset_id,
                project_id,
                media_type,
                display_name(filename),
                rel,
                size,
                digest.hexdigest(),
                dumps(metadata),
                job_id,
                utcnow(),
            ),
        )
    except BaseException:
        dest.unlink(missing_ok=True)
        raise
    return get(conn, asset_id)
