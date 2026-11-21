"""PNG export with or without generation details (PRD phase I4, ADR-0008).

Engines save bare PNGs; generation details live in the asset record. An export
either carries a short provenance label as PNG text chunks (the default: says
the picture is AI-generated, which engine and model revision, and the seed) or
carries no text at all. The description (prompt) is never written into a file:
it is private and stays in Rasva.

Either way, any text, EXIF or timestamp chunks already in the file are removed
first, so an export never leaks something the owner can't see. Pixels are
untouched. Standard library only: the app's environment has no Pillow.

JPEG is encoded in the browser (ADR-0008), using ``provenance()`` for its
comment segment.
"""

from __future__ import annotations

import struct
import zlib
from typing import Any

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
# Ancillary chunks that can carry text, metadata or identifying details.
METADATA_CHUNKS = {b"tEXt", b"zTXt", b"iTXt", b"eXIf", b"tIME"}
MAX_TEXT = 200


class ExportError(ValueError):
    pass


def _chunks(data: bytes) -> list[tuple[bytes, bytes]]:
    if not data.startswith(PNG_SIGNATURE):
        raise ExportError("This file is not a PNG image.")
    out: list[tuple[bytes, bytes]] = []
    pos = len(PNG_SIGNATURE)
    while pos < len(data):
        if pos + 8 > len(data):
            raise ExportError("The PNG file is damaged.")
        (length,) = struct.unpack(">I", data[pos : pos + 4])
        ctype = data[pos + 4 : pos + 8]
        end = pos + 12 + length
        if end > len(data):
            raise ExportError("The PNG file is damaged.")
        body = data[pos + 8 : pos + 8 + length]
        (crc,) = struct.unpack(">I", data[pos + 8 + length : end])
        if zlib.crc32(ctype + body) != crc:
            raise ExportError("The PNG file is damaged.")
        out.append((ctype, body))
        pos = end
        if ctype == b"IEND":
            break
    if not out or out[0][0] != b"IHDR" or out[-1][0] != b"IEND":
        raise ExportError("The PNG file is damaged.")
    return out


def _chunk(ctype: bytes, body: bytes) -> bytes:
    return struct.pack(">I", len(body)) + ctype + body + struct.pack(">I", zlib.crc32(ctype + body))


def _latin1(text: str) -> bytes:
    clean = "".join(ch if 32 <= ord(ch) < 127 else "?" for ch in text)
    return clean[:MAX_TEXT].encode("latin-1")


def provenance(asset: dict[str, Any], engine_name: str | None = None) -> dict[str, str]:
    """PNG keyword -> text. Never includes the description (prompt)."""
    meta = asset.get("metadata") or {}
    if asset.get("kind") != "output" or not meta.get("synthetic"):
        return {}
    engine = engine_name or str(meta.get("engine") or "an image engine")
    revision = meta.get("model_revision")
    source = engine + (f" (model revision {str(revision)[:12]})" if revision else "")
    comment = f"AI-generated image (synthetic), made locally with {source}."
    if meta.get("seed") is not None:
        comment += f" Seed {meta['seed']}."
    return {"Software": "Rasva Studio", "Source": source, "Comment": comment}


def export_png(data: bytes, details: dict[str, str] | None) -> bytes:
    """Remove all text/metadata chunks; with ``details``, add them as tEXt after IHDR."""
    chunks = [(t, b) for t, b in _chunks(data) if t not in METADATA_CHUNKS]
    text = [
        _chunk(b"tEXt", _latin1(k)[:79] + b"\x00" + _latin1(v)) for k, v in (details or {}).items()
    ]
    parts = [PNG_SIGNATURE, _chunk(*chunks[0]), *text, *(_chunk(t, b) for t, b in chunks[1:])]
    return b"".join(parts)


def text_chunks(data: bytes) -> dict[str, str]:
    """tEXt keyword -> text (for tests and checks)."""
    found = {}
    for ctype, body in _chunks(data):
        if ctype == b"tEXt":
            key, _, value = body.partition(b"\x00")
            found[key.decode("latin-1")] = value.decode("latin-1")
    return found


def export_name(asset: dict[str, Any], ext: str) -> str:
    seed = (asset.get("metadata") or {}).get("seed")
    stem = f"rasva-image-seed-{seed}" if seed is not None else f"rasva-image-{asset['id'][-8:]}"
    return f"{stem}.{ext}"
