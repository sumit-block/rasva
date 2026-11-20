"""Locating optional media tools (PRD §21: FFmpeg for MP3 and compressed imports)."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

# Apps started from Finder do not inherit the shell PATH, so also look where
# Homebrew and MacPorts install FFmpeg.
_COMMON = ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg", "/opt/local/bin/ffmpeg")


def find_ffmpeg() -> str | None:
    override = os.environ.get("RASVA_FFMPEG")
    if override:
        return override if Path(override).is_file() else None
    found = shutil.which("ffmpeg")
    if found:
        return found
    return next((p for p in _COMMON if Path(p).is_file()), None)
