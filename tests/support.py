"""Helpers shared by the tests."""

from __future__ import annotations

import pytest

from mkvtranscode.util import which


def has_ffmpeg() -> bool:
    return bool(which("ffmpeg") and which("ffprobe"))


needs_ffmpeg = pytest.mark.skipif(not has_ffmpeg(), reason="ffmpeg or ffprobe not installed")
