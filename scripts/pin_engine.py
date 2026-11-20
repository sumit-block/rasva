#!/usr/bin/env python3
"""Pin an engine's model files to exact Hugging Face commits with SHA-256 per file.

    python3 scripts/pin_engine.py chatterbox
    python3 scripts/pin_engine.py indicf5 [--reviewed-code]
    python3 scripts/pin_engine.py kokoro            (same as scripts/pin_kokoro.py)

Writes backend/rasva/engines/<engine>/manifest.json, which the installer uses to
download and verify every file. Needs internet access to huggingface.co.
Standard library only. Set HF_TOKEN for repositories that require accepting terms.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINES = ROOT / "backend" / "rasva" / "engines"

SPECS = {
    "chatterbox": {
        "engine": "chatterbox-multilingual",
        "dir": "chatterbox",
        "sources": [{
            "repo": "ResembleAI/chatterbox", "subdir": "",
            "files": ["ve.pt", "t3_mtl23ls_v2.safetensors", "s3gen.pt",
                      "grapheme_mtl_merged_expanded_v1.json", "conds.pt"],
        }],
    },
    "indicf5": {
        "engine": "indicf5",
        "dir": "indicf5",
        "sources": [
            # The whole repository is listed so the layout can be confirmed by hand.
            {"repo": "ai4bharat/IndicF5", "subdir": "", "files": "*"},
            {"repo": "charactr/vocos-mel-24khz", "subdir": "vocos",
             "files": ["config.yaml", "pytorch_model.bin"]},
        ],
        "layout_note": (
            "Fill \"layout\" in the manifest after checking the IndicF5 repository: "
            '{"checkpoint": "<weights file>", "vocab": "<vocab.txt path>", '
            '"model_cfg": {...DiT settings from its config...}, "use_ema": true}'
        ),
    },
}  # fmt: skip
SKIP = {".gitattributes", "README.md"}


def _get(url: str) -> urllib.request.Request:
    headers = {"User-Agent": "rasva-pin/1"}
    if os.environ.get("HF_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['HF_TOKEN']}"
    return urllib.request.Request(url, headers=headers)  # noqa: S310 - fixed https host


def get_json(url: str) -> object:
    with urllib.request.urlopen(_get(url), timeout=60) as resp:  # noqa: S310
        return json.load(resp)


def sha256_url(url: str) -> tuple[str, int]:
    digest, size = hashlib.sha256(), 0
    with urllib.request.urlopen(_get(url), timeout=300) as resp:  # noqa: S310
        while chunk := resp.read(1 << 20):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def pin_source(
    repo: str, wanted: list[str] | str, revision: str
) -> tuple[str, list[dict], list[str]]:
    api = f"https://huggingface.co/api/models/{repo}"
    commit = get_json(f"{api}/revision/{urllib.parse.quote(revision)}")["sha"]
    tree = {
        e["path"]: e
        for e in get_json(f"{api}/tree/{commit}?recursive=true&expand=true")
        if e.get("type") == "file"
    }
    paths = sorted(p for p in tree if p not in SKIP) if wanted == "*" else list(wanted)
    missing = [p for p in paths if p not in tree]
    if missing:
        raise SystemExit(f"{repo}@{commit} is missing: {missing}")
    files, code = [], []
    for path in paths:
        entry = tree[path]
        lfs = entry.get("lfs")
        if lfs and lfs.get("oid"):
            sha, size = lfs["oid"], int(lfs["size"])  # LFS oid is the content SHA-256
        else:
            sha, size = sha256_url(f"https://huggingface.co/{repo}/resolve/{commit}/{path}")
        files.append({"path": path, "sha256": sha, "size_bytes": size})
        if path.endswith(".py"):
            code.append(path)
        print(f"  {repo}/{path:40} {size:>14,}  {sha[:16]}…")
    return commit, files, code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("engine", choices=[*SPECS, "kokoro"])
    parser.add_argument("--revision", default="main")
    parser.add_argument("--reviewed-code", action="store_true",
                        help="confirm you have read every .py file listed "
                             "(Rasva never runs them; know what the snapshot holds)")  # fmt: skip
    args = parser.parse_args()
    if args.engine == "kokoro":
        return subprocess.call([sys.executable, str(ROOT / "scripts" / "pin_kokoro.py"),
                                "--revision", args.revision])  # fmt: skip

    spec = SPECS[args.engine]
    sources, all_code = [], []
    for src in spec["sources"]:
        commit, files, code = pin_source(src["repo"], src["files"], args.revision)
        sources.append(
            {"repo": src["repo"], "revision": commit, "subdir": src["subdir"], "files": files}
        )
        all_code += [f"{src['repo']}/{c}" for c in code]
    if all_code and not args.reviewed_code:
        print("\nThe snapshot contains Python files:\n  " + "\n  ".join(all_code))
        print("Rasva never runs them, but review them, then re-run with --reviewed-code.")
        return 2
    target = ENGINES / spec["dir"] / "manifest.json"
    previous = json.loads(target.read_text()) if target.exists() else {}
    manifest = {"engine": spec["engine"], "sources": sources}
    if "layout" in previous:
        manifest["layout"] = previous["layout"]
    target.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\nWrote {target.relative_to(ROOT)}")
    if spec.get("layout_note") and "layout" not in manifest:
        print(spec["layout_note"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
