#!/usr/bin/env python3
"""Pin Kokoro-82M model files to one Hugging Face commit with SHA-256 per file.

Writes backend/rasva/engines/kokoro/manifest.json, which the installer uses to
download and verify every file. Run it once (and again only to upgrade):

    python3 scripts/pin_kokoro.py                 # pin the current 'main' commit
    python3 scripts/pin_kokoro.py --revision <sha>

Needs internet access to huggingface.co. Standard library only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

REPO = "hexgrad/Kokoro-82M"
API = f"https://huggingface.co/api/models/{REPO}"
ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "backend" / "rasva" / "engines" / "kokoro" / "manifest.json"
# Must match VOICE_CATALOGUE in rasva/engines/kokoro/__init__.py.
VOICES = [
    "af_heart", "af_bella", "af_nicole", "am_michael", "am_adam", "bf_emma", "bm_george",
    "hf_alpha", "hf_beta", "hm_omega", "hm_psi",
]  # fmt: skip
FILES = ["config.json", "kokoro-v1_0.pth", *[f"voices/{v}.pt" for v in VOICES]]


def get_json(url: str) -> object:
    # Only fixed https://huggingface.co URLs are ever opened here.
    req = urllib.request.Request(url, headers={"User-Agent": "rasva-pin/1"})  # noqa: S310
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310
        return json.load(resp)


def sha256_url(url: str) -> tuple[str, int]:
    digest, size = hashlib.sha256(), 0
    req = urllib.request.Request(url, headers={"User-Agent": "rasva-pin/1"})  # noqa: S310
    with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310
        while chunk := resp.read(1 << 20):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--revision", default="main", help="branch, tag or commit to pin")
    args = parser.parse_args()

    commit = get_json(f"{API}/revision/{urllib.parse.quote(args.revision)}")["sha"]
    print(f"Pinning {REPO} at commit {commit}")
    tree = {
        e["path"]: e
        for e in get_json(f"{API}/tree/{commit}?recursive=true&expand=true")
        if e.get("type") == "file"
    }
    missing = [f for f in FILES if f not in tree]
    if missing:
        print(f"These files are not in the repository at {commit}: {missing}", file=sys.stderr)
        return 1

    files = []
    for path in FILES:
        entry = tree[path]
        lfs = entry.get("lfs")
        if lfs and lfs.get("oid"):
            # The LFS object id is the SHA-256 of the file contents.
            sha, size = lfs["oid"], int(lfs["size"])
        else:
            sha, size = sha256_url(f"https://huggingface.co/{REPO}/resolve/{commit}/{path}")
        files.append({"path": path, "sha256": sha, "size_bytes": size})
        print(f"  {path:28} {size:>12,} bytes  {sha[:16]}…")

    MANIFEST.write_text(
        json.dumps({"repo": REPO, "revision": commit, "files": files}, indent=2) + "\n",
        encoding="utf-8",
    )
    total = sum(f["size_bytes"] for f in files)
    print(f"Wrote {MANIFEST.relative_to(ROOT)} ({len(files)} files, {total / 1024**2:.0f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
