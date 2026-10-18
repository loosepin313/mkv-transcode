"""Shared test fixtures.

The tests build a small Matroska file with ffmpeg. Tests that need ffmpeg or
ffprobe skip when those binaries are absent.
"""

from __future__ import annotations

import os
import subprocess

import pytest

from mkvtranscode.config import Config

SAMPLE_NAME = "Alien.1979.1080p.BluRay.DTS-HD.MA.5.1.mkv"


@pytest.fixture()
def config(tmp_path) -> Config:
    """A Config that points at a temporary directory tree."""
    cfg = Config()
    cfg.paths.incoming = str(tmp_path / "incoming")
    cfg.paths.transcoded = str(tmp_path / "transcoded")
    cfg.paths.work = str(tmp_path / "transcoded" / ".work")
    cfg.paths.logs = str(tmp_path / "transcoded" / ".logs")
    cfg.paths.processed = str(tmp_path / "transcoded" / ".processed")
    cfg.paths.imdb_data = str(tmp_path / "imdb")
    cfg.watch.min_size_mb = 0.0
    cfg.watch.stable_seconds = 0.0
    cfg.watch.check_open_files = False
    cfg.metadata.provider = "filename"
    cfg.video.av1.preset = 12
    cfg.video.av1.crf = 45
    cfg.job.min_free_gb = 0.0
    cfg.resolve().validate()
    for path in (cfg.paths.incoming, cfg.paths.transcoded, cfg.paths.work, cfg.paths.logs):
        os.makedirs(path, exist_ok=True)
    return cfg


@pytest.fixture()
def sample(tmp_path, config) -> str:
    """A Matroska file with one video, three audio and two subtitle tracks."""
    srt_en = tmp_path / "en.srt"
    srt_es = tmp_path / "es.srt"
    srt_en.write_text(
        "1\n00:00:00,000 --> 00:00:02,000\nHello world\n\n"
        "2\n00:00:02,000 --> 00:00:04,000\nSecond line\n",
        encoding="utf-8",
    )
    srt_es.write_text("1\n00:00:00,000 --> 00:00:02,000\nHola mundo\n", encoding="utf-8")
    target = os.path.join(config.paths.incoming, SAMPLE_NAME)
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=24:duration=4",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
        "-f", "lavfi", "-i", "sine=frequency=880:duration=4",
        "-f", "lavfi", "-i", "sine=frequency=220:duration=4",
        "-i", str(srt_en),
        "-i", str(srt_es),
        "-map", "0:v", "-c:v", "mpeg2video", "-q:v", "8",
        "-map", "1:a", "-c:a:0", "ac3", "-b:a:0", "192k", "-channel_layout", "5.1",
        "-metadata:s:a:0", "language=eng",
        "-map", "2:a", "-c:a:1", "aac", "-b:a:1", "128k", "-metadata:s:a:1", "language=spa",
        "-map", "3:a", "-c:a:2", "pcm_s16le", "-metadata:s:a:2", "language=eng",
        "-metadata:s:a:2", "title=Commentary",
        "-map", "4:s", "-c:s:0", "srt", "-metadata:s:s:0", "language=eng",
        "-map", "5:s", "-c:s:1", "srt", "-metadata:s:s:1", "language=spa",
        "-metadata", "title=Alien (1979)",
        target,
    ]
    subprocess.run(command, check=True, capture_output=True, text=True)
    return target


@pytest.fixture()
def interlaced_sample(tmp_path, config) -> str:
    """A 480i Matroska file, the shape a DVD rip has."""
    target = os.path.join(config.paths.incoming, "Interlaced.DVD.1979.mkv")
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=720x480:rate=29.97:duration=3",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
        "-c:v", "mpeg2video", "-flags", "+ildct+ilme", "-g", "15",
        "-c:a", "ac3", "-b:a", "192k", "-metadata:s:a:0", "language=eng",
        target,
    ]
    subprocess.run(command, check=True, capture_output=True, text=True)
    return target


@pytest.fixture()
def imdb_index(tmp_path, config):
    """Build the IMDb SQLite index from the small fixture files."""
    from mkvtranscode.metadata.imdb_dataset import ImdbDatasetProvider

    fixtures = os.path.join(os.path.dirname(__file__), "fixtures")
    provider = ImdbDatasetProvider(
        data_dir=config.paths.imdb_data,
        title_types=["movie", "tvMovie", "video"],
        include_akas=True,
        akas_countries=["XWW"],
    )
    provider.fetch(
        basics_file=os.path.join(fixtures, "basics.tsv.gz"),
        akas_file=os.path.join(fixtures, "akas.tsv.gz"),
    )
    return provider
