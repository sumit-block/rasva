"""Pinned model-file manifests for engines whose files come from several repos.

Format (written by scripts/pin_engine.py):
{
  "engine": "<engine id>",
  "sources": [
    {"repo": "owner/name", "revision": "<commit sha>", "subdir": "",
     "files": [{"path": "...", "sha256": "...", "size_bytes": 123}]}
  ],
  "layout": {...}            # optional, engine-specific (e.g. which file is the checkpoint)
}
Every URL is pinned to the commit, never a branch.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from rasva.engines.contract import ModelArtifact

_COMMIT = re.compile(r"^[0-9a-f]{40}$")


class ManifestError(ValueError):
    pass


def load(path: Path, engine_id: str, repos: set[str]) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("engine") != engine_id:
        raise ManifestError(f"{path.name} is for a different engine")
    seen = {s.get("repo") for s in data.get("sources", [])}
    if seen != repos:
        raise ManifestError(f"{path.name} must pin exactly {sorted(repos)}, found {sorted(seen)}")
    for s in data["sources"]:
        if not _COMMIT.match(str(s.get("revision", ""))):
            raise ManifestError(f"{s['repo']} is not pinned to a full commit hash")
    return data


def artifacts(manifest: dict[str, Any] | None) -> list[ModelArtifact]:
    if not manifest:
        return []
    out = []
    for s in manifest["sources"]:
        base = f"https://huggingface.co/{s['repo']}/resolve/{s['revision']}"
        prefix = f"{s['subdir'].strip('/')}/" if s.get("subdir") else ""
        for f in s["files"]:
            out.append(
                ModelArtifact(
                    path=f"{prefix}{f['path']}", url=f"{base}/{f['path']}",
                    sha256=f["sha256"], size_bytes=f["size_bytes"],
                )
            )  # fmt: skip
    return out


def revisions(manifest: dict[str, Any] | None) -> dict[str, str]:
    return {s["repo"]: s["revision"] for s in (manifest or {}).get("sources", [])}
