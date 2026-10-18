"""Turn a source file name into a clean title, year and output path.

MakeMKV and similar tools keep the disc label. Labels look like
'Alien.1979.1080p.BluRay.DTS-HD.MA.5.1.mkv'. This module removes the noise and
keeps the title, the year and any IMDb id or episode id.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

from .util import imdb_id_from_text, sanitize_component

# Noise that release groups and disc rippers add to a file name.
_NOISE_WORDS = {
    "bluray", "blu-ray", "blu", "br", "bd", "bd50", "bd25", "dvd", "dvd5", "dvd9",
    "uhd", "hd", "fhd", "sdr", "hdr", "hdr10", "hdr10plus", "dolbyvision", "dv",
    "x264", "x265", "h264", "h265", "hevc", "av1", "avc", "mpeg2", "vc1", "divx",
    "remux", "web", "web-dl", "webdl", "webrip", "hdtv", "hdcam", "cam", "ts",
    "aac", "ac3", "eac3", "dts", "dts-hd", "ma", "truehd", "thd", "atmos", "flac",
    "mp3", "opus", "pcm", "5 1", "7 1", "2 0", "5 1 2",
    "pal", "ntsc", "1080p", "1080i", "2160p", "720p", "480p", "4k", "8k",
    "proper", "repack", "retail", "unrated", "theatrical", "extended",
    "imax", "remastered", "limited", "multi", "dual", "dl", "rip", "screener",
    "mkv", "mp4", "m4v", "avi", "ts", "m2ts", "wmv",
}
# Resolution markers. These are noise. A number that is part of a real title,
# such as 2049 or 300, stays in the title.
_RESOLUTION_RE = re.compile(r"^(?:\d{3,4}[pi]|4k|8k)$")
_YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
_EPISODE_RE = re.compile(r"\b[sS](\d{1,2})[\s._-]?[eE](\d{1,3})\b")
_EPISODE_ALT_RE = re.compile(r"\b(\d{1,2})x(\d{1,3})\b")
_PART_RE = re.compile(r"\b(?:part|pt|disc|cd)[\s._-]*(\d{1,2})\b", re.IGNORECASE)
_CHANNEL_RE = re.compile(r"\b\d{1,2}[.,]\d(?:[.,]\d)?\b")  # 5.1, 7.1, 2.0, 5.1.2
_BRACKET_RE = re.compile(r"[\[\](){}]")


@dataclass
class Guess:
    """The best reading of a file name before any metadata lookup."""

    raw: str
    title: str
    year: Optional[int] = None
    imdb_id: Optional[str] = None
    episode: Optional[str] = None
    part: Optional[int] = None

    @property
    def has_episode(self) -> bool:
        return bool(self.episode)


def parse_filename(path: str) -> Guess:
    """Parse a file path into a Guess."""
    stem = os.path.splitext(os.path.basename(path))[0]
    guess = Guess(raw=stem, title=stem)

    guess.imdb_id = imdb_id_from_text(stem)
    work = _BRACKET_RE.sub(" ", stem)
    work = re.sub(r"\btt\d{7,8}\b", " ", work, flags=re.IGNORECASE)
    # Remove channel layouts before the split, so '5.1' does not leave '5' and '1'.
    work = _CHANNEL_RE.sub(" ", work)

    episode = _EPISODE_RE.search(work) or _EPISODE_ALT_RE.search(work)
    if episode:
        guess.episode = "s%02de%02d" % (int(episode.group(1)), int(episode.group(2)))
        work = work[: episode.start()] + " " + work[episode.end() :]

    part = _PART_RE.search(work)
    if part:
        guess.part = int(part.group(1))
        work = work[: part.start()] + " " + work[part.end() :]

    guess.year, work = _pick_year(work)

    tokens = re.split(r"[\s._\-]+", work)
    kept: List[str] = []
    for token in tokens:
        if not token:
            continue
        lowered = token.lower()
        if lowered in _NOISE_WORDS:
            continue
        if _RESOLUTION_RE.match(lowered):
            continue
        if re.fullmatch(r"\d{1,2}[.,]\d{1,2}", token):
            continue
        kept.append(token)
    title = " ".join(kept).strip()
    title = re.sub(r"\s{2,}", " ", title)
    guess.title = title or stem
    return guess


def _pick_year(text: str) -> Tuple[Optional[int], str]:
    """Find the release year and remove it from the text."""
    matches = list(_YEAR_RE.finditer(text))
    if not matches:
        return None, text
    chosen = None
    # Prefer a year that stands alone at the end of the name.
    for match in reversed(matches):
        before = text[: match.start()]
        after = text[match.end() :]
        if (not before.strip() or before.strip()[-1] in "._- ([") and (
            not after.strip() or after.strip()[0] in "._- )["
        ):
            chosen = match
            break
    if chosen is None:
        # Reject a leading year such as the 2001 in "2001 A Space Odyssey".
        for match in reversed(matches):
            if not text[: match.start()].strip():
                continue
            chosen = match
            break
    if chosen is None:
        return None, text
    # Drop a year that is only a resolution or a number in the middle of words.
    value = int(chosen.group(1))
    remainder = (text[: chosen.start()] + " " + text[chosen.end() :]).strip()
    if value < 1900 or value > 2100:
        return None, text
    return value, remainder


def format_episode(episode: Optional[str]) -> str:
    """Return an episode id in the form s01e02."""
    return episode or ""


def build_output_path(
    root: str,
    title: str,
    year: Optional[int],
    pattern: str,
    episode: Optional[str] = None,
    imdb_id: Optional[str] = None,
    part: Optional[int] = None,
    container: str = "mkv",
) -> str:
    """Build the output path under root from the naming pattern."""
    safe_title = sanitize_component(title)
    year_text = str(year) if year else ""
    part_text = ""
    if part and part > 1:
        part_text = " (Part %d)" % part
    fields = {
        "title": safe_title,
        "year": year_text,
        "episode": format_episode(episode),
        "imdb_id": imdb_id or "",
        "part": part_text,
        "ext": container.lstrip("."),
    }
    try:
        relative = pattern.format(**fields)
    except KeyError as exc:
        raise ValueError("naming pattern uses an unknown field: %s" % exc)
    if not year_text:
        # Drop the empty ' ()' that a missing year leaves behind.
        relative = relative.replace(" ()", "").replace("()", "")
    pieces = [sanitize_component(piece) for piece in relative.split("/") if piece.strip()]
    if not pieces:
        raise ValueError("naming pattern produced an empty path")
    # The file name must keep its extension.
    last = pieces[-1]
    if not last.endswith("." + fields["ext"]):
        pieces[-1] = (last or safe_title) + (" (%s)" % year_text if year_text else "")
        pieces[-1] = pieces[-1] + "." + fields["ext"]
    return os.path.join(root, *pieces)


def unique_path(path: str) -> str:
    """Return a path that does not exist yet, adding ' (2)', ' (3)' as needed."""
    if not os.path.exists(path):
        return path
    stem, ext = os.path.splitext(path)
    for counter in range(2, 100):
        candidate = "%s (%d)%s" % (stem, counter, ext)
        if not os.path.exists(candidate):
            return candidate
    raise ValueError("cannot find a free name near %s" % path)
