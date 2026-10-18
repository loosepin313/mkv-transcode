"""Tests for the ffprobe wrapper."""

import pytest

from mkvtranscode import probe
from mkvtranscode.errors import ProbeError

from support import needs_ffmpeg

SAMPLE_JSON = {
    "format": {"format_name": "matroska,webm", "duration": "120.0", "size": "1000"},
    "streams": [
        {
            "index": 0, "codec_type": "video", "codec_name": "hevc",
            "width": 1920, "height": 1080, "pix_fmt": "yuv420p10le",
            "field_order": "progressive", "color_primaries": "bt2020",
            "color_transfer": "smpte2084", "color_space": "bt2020nc",
            "avg_frame_rate": "24000/1001",
        },
        {"index": 1, "codec_type": "audio", "codec_name": "truehd", "channels": 8,
         "tags": {"language": "eng"}},
        {"index": 2, "codec_type": "audio", "codec_name": "ac3", "channels": 6,
         "tags": {"language": "spa"}},
        {"index": 3, "codec_type": "subtitle", "codec_name": "subrip",
         "tags": {"language": "eng"}},
        {"index": 4, "codec_type": "subtitle", "codec_name": "hdmv_pgs_subtitle",
         "tags": {"language": "fra"}},
    ],
}


def test_parse_reports_streams_and_positions():
    info = probe.parse(SAMPLE_JSON, "/tmp/x.mkv")
    assert len(info.streams) == 5
    assert info.bit_depth() == 10
    assert info.is_hdr() is True
    assert info.is_interlaced() is False
    assert len(info.audios()) == 2
    assert info.position_in_type(info.audios()[1]) == 1
    assert info.position_in_type(info.videos()[0]) == 0
    assert info.primary_video().height == 1080


def test_interlaced_detection():
    data = {"format": {}, "streams": [dict(SAMPLE_JSON["streams"][0], field_order="tt")]}
    info = probe.parse(data, "/tmp/x.mkv")
    assert info.is_interlaced() is True


def test_largest_video_wins():
    data = {"format": {}, "streams": [
        {"index": 0, "codec_type": "video", "codec_name": "mpeg2video", "width": 64, "height": 64},
        {"index": 1, "codec_type": "video", "codec_name": "mpeg2video", "width": 1920, "height": 1080},
    ]}
    info = probe.parse(data, "/tmp/x.mkv")
    assert info.primary_video().index == 1


def test_missing_video_raises():
    info = probe.parse({"format": {}, "streams": []}, "/tmp/x.mkv")
    with pytest.raises(ProbeError):
        info.primary_video()


@needs_ffmpeg
def test_ffprobe_on_a_real_file(sample):
    info = probe.ffprobe(sample)
    assert info.videos()
    assert len(info.audios()) == 3
    assert len(info.subtitles()) == 2
    assert info.audios()[0].language == "eng"
    assert info.duration > 3.0


def test_ffprobe_missing_file():
    with pytest.raises(ProbeError):
        probe.ffprobe("/nonexistent/nope.mkv")
