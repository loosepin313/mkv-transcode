"""Read media facts with ffprobe.

The pipeline needs the stream list, the colour metadata and the field order.
ffprobe returns all of that as JSON.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Dict, List, Optional

from .errors import ProbeError
from .util import which

HDR_TRANSFERS = ("smpte2084", "arib-std-b67", "smpte432")
BT2020_PRIMARIES = ("bt2020", "bt709")  # bt709 primaries with bt2020 space is rare
INTERLACED_FIELDS = ("tt", "bb", "tb", "bt")
IMAGE_SUBTITLES = ("hdmv_pgs_subtitle", "dvd_subtitle", "dvb_subtitle", "subrip_image")


@dataclass
class Stream:
    index: int
    codec_type: str
    codec_name: str
    language: Optional[str] = None
    title: Optional[str] = None
    default: bool = False
    forced: bool = False
    channels: Optional[int] = None
    channel_layout: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    pix_fmt: Optional[str] = None
    bit_depth: Optional[int] = None
    profile: Optional[str] = None
    field_order: Optional[str] = None
    color_primaries: Optional[str] = None
    color_transfer: Optional[str] = None
    color_space: Optional[str] = None
    avg_frame_rate: Optional[str] = None
    sample_aspect_ratio: Optional[str] = None
    duration: Optional[float] = None
    bit_rate: Optional[int] = None
    tags: Dict[str, str] = field(default_factory=dict)

    @property
    def label(self) -> str:
        """A short human description such as 'eng DTS-HD MA 7.1'."""
        parts = [self.codec_name or "?"]
        if self.language:
            parts.insert(0, self.language)
        if self.channels:
            parts.append("%d.%d" % (self.channels - 1, 1) if self.channels > 1 else "1.0")
        if self.title:
            parts.append(self.title)
        return " ".join(parts)

    @property
    def fps(self) -> Optional[float]:
        """The average frame rate as a float, or None."""
        if not self.avg_frame_rate:
            return None
        try:
            return float(Fraction(self.avg_frame_rate))
        except (ValueError, ZeroDivisionError):
            return None


@dataclass
class MediaInfo:
    path: str
    format_name: str = ""
    format_long: str = ""
    duration: float = 0.0
    size: int = 0
    bit_rate: int = 0
    title: Optional[str] = None
    streams: List[Stream] = field(default_factory=list)
    chapters: List[Dict[str, object]] = field(default_factory=list)

    def streams_of(self, codec_type: str) -> List[Stream]:
        """Return every stream of one type."""
        return [s for s in self.streams if s.codec_type == codec_type]

    def position_in_type(self, stream: Stream) -> int:
        """Return the position of a stream among streams of its type.

        ffmpeg stream specifiers such as '-map 0:a:2' count within the type,
        not across the whole file. ffprobe reports the position across the
        whole file, so the pipeline needs both numbers.
        """
        position = 0
        for other in self.streams:
            if other.codec_type != stream.codec_type:
                continue
            if other.index == stream.index:
                return position
            position += 1
        return 0

    def videos(self) -> List[Stream]:
        return self.streams_of("video")

    def audios(self) -> List[Stream]:
        return self.streams_of("audio")

    def subtitles(self) -> List[Stream]:
        return self.streams_of("subtitle")

    def primary_video(self) -> Stream:
        """Return the largest video stream. That is the main feature."""
        videos = self.videos()
        if not videos:
            raise ProbeError("%s has no video stream" % self.path)
        return max(videos, key=lambda s: (s.width or 0) * (s.height or 0))

    def is_hdr(self) -> bool:
        """True when the main video carries HDR transfer or bt2020 primaries."""
        video = self.primary_video()
        if (video.color_transfer or "") in HDR_TRANSFERS:
            return True
        if (video.color_primaries or "") == "bt2020" and (video.color_space or "") == "bt2020nc":
            return True
        return False

    def is_interlaced(self) -> bool:
        """True when the main video carries interlaced fields."""
        video = self.primary_video()
        return (video.field_order or "") in INTERLACED_FIELDS

    def bit_depth(self) -> int:
        """The bit depth of the main video, usually 8 or 10."""
        video = self.primary_video()
        if video.bit_depth:
            return video.bit_depth
        pix = video.pix_fmt or ""
        for depth in (16, 12, 10):
            if "%sle" % depth in pix or "%sbe" % depth in pix:
                return depth
        return 8

    def height(self) -> int:
        video = self.primary_video()
        return video.height or 0

    def audio_bitrate_total(self) -> int:
        """The combined bit rate of all audio streams, 0 when unknown."""
        return sum(s.bit_rate or 0 for s in self.audios())


def ffprobe(path: str, timeout: int = 120) -> MediaInfo:
    """Run ffprobe on a file and return a MediaInfo object."""
    binary = which("ffprobe") or "ffprobe"
    if not os.path.exists(path):
        raise ProbeError("input does not exist: %s" % path)
    cmd = [
        binary,
        "-v", "error",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        "-show_chapters",
        path,
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProbeError("ffprobe failed to run: %s" % exc)
    if result.returncode != 0:
        detail = (result.stderr or "").strip().splitlines()
        raise ProbeError("ffprobe failed: %s" % (detail[-1] if detail else "exit %d" % result.returncode))
    try:
        data = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise ProbeError("ffprobe returned invalid JSON: %s" % exc)
    return parse(data, path)


def parse(data: dict, path: str) -> MediaInfo:
    """Build a MediaInfo object from an ffprobe JSON document."""
    fmt = data.get("format") or {}
    info = MediaInfo(
        path=path,
        format_name=fmt.get("format_name", ""),
        format_long=fmt.get("format_long_name", ""),
        duration=_as_float(fmt.get("duration")) or 0.0,
        size=int(_as_float(fmt.get("size")) or 0),
        bit_rate=int(_as_float(fmt.get("bit_rate")) or 0),
        title=(fmt.get("tags") or {}).get("title"),
        chapters=data.get("chapters") or [],
    )
    for raw in data.get("streams") or []:
        info.streams.append(_stream(raw))
    return info


def _stream(raw: dict) -> Stream:
    tags = {str(k): str(v) for k, v in (raw.get("tags") or {}).items()}
    # ffprobe nests the flags: "disposition": {"default": 1, "forced": 0}
    disposition = raw.get("disposition") or {}
    bit_depth = None
    if raw.get("bits_per_raw_sample"):
        try:
            bit_depth = int(raw["bits_per_raw_sample"])
        except (TypeError, ValueError):
            bit_depth = None
    return Stream(
        index=int(raw.get("index", -1)),
        codec_type=raw.get("codec_type", ""),
        codec_name=raw.get("codec_name") or "",
        language=raw.get("tags", {}).get("language") or None,
        title=raw.get("tags", {}).get("title") or None,
        default=bool(disposition.get("default")),
        forced=bool(disposition.get("forced")),
        channels=raw.get("channels"),
        channel_layout=raw.get("channel_layout"),
        width=raw.get("width"),
        height=raw.get("height"),
        pix_fmt=raw.get("pix_fmt"),
        bit_depth=bit_depth,
        profile=raw.get("profile"),
        field_order=raw.get("field_order"),
        color_primaries=raw.get("color_primaries"),
        color_transfer=raw.get("color_transfer"),
        color_space=raw.get("color_space"),
        avg_frame_rate=raw.get("avg_frame_rate"),
        sample_aspect_ratio=raw.get("sample_aspect_ratio"),
        duration=_as_float(raw.get("duration")),
        bit_rate=int(_as_float(raw.get("bit_rate")) or 0) or None,
        tags=tags,
    )


def _as_float(value) -> Optional[float]:
    try:
        if value in (None, "N/A", ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
