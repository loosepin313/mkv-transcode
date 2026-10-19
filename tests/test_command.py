"""Tests for the ffmpeg plan builder."""

from mkvtranscode import probe
from mkvtranscode.transcode.command import build_plan

from support import needs_ffmpeg

SAMPLE_JSON = {
    "format": {"format_name": "matroska", "duration": "600.0", "size": "8000000000"},
    "streams": [
        {"index": 0, "codec_type": "video", "codec_name": "mpeg2video", "width": 1920,
         "height": 1080, "pix_fmt": "yuv420p", "field_order": "progressive"},
        {"index": 1, "codec_type": "audio", "codec_name": "ac3", "channels": 6,
         "bit_rate": "448000", "tags": {"language": "spa"}},
        {"index": 2, "codec_type": "audio", "codec_name": "truehd", "channels": 8,
         "bit_rate": "3000000", "tags": {"language": "eng"}},
        {"index": 3, "codec_type": "audio", "codec_name": "dts", "channels": 6,
         "bit_rate": "1500000", "tags": {"language": "eng", "title": "Commentary"}},
        {"index": 4, "codec_type": "subtitle", "codec_name": "subrip", "tags": {"language": "spa"}},
        {"index": 5, "codec_type": "subtitle", "codec_name": "hdmv_pgs_subtitle",
         "tags": {"language": "eng"}},
    ],
}


def info_for(overrides=None):
    data = {"format": dict(SAMPLE_JSON["format"]), "streams": [dict(s) for s in SAMPLE_JSON["streams"]]}
    for stream, patch in (overrides or {}).items():
        data["streams"][stream].update(patch)
    return probe.parse(data, "/in/movie.mkv")


def test_smart_policy_copies_compatible_tracks_and_encodes_the_rest(config):
    plan = build_plan(config, info_for(), "/out/movie.mkv")
    actions = {d.language: (d.action, d.codec) for d in plan.audio}
    assert actions["spa"] == ("copy", "ac3")
    assert actions["eng"][0] == "encode"
    assert actions["eng"][1] == "libopus"


def test_english_tracks_come_first_and_the_first_is_default(config):
    plan = build_plan(config, info_for(), "/out/movie.mkv")
    assert [d.language for d in plan.audio] == ["eng", "eng", "spa"]
    assert plan.audio[0].default is True
    assert plan.audio[2].default is False
    command = " ".join(plan.command(0))
    assert "-disposition:a:0 default" in command


def test_map_uses_the_position_inside_the_stream_type(config):
    plan = build_plan(config, info_for(), "/out/movie.mkv")
    maps = [plan.command(0)[i + 1] for i, part in enumerate(plan.command(0)) if part == "-map"]
    assert maps == ["0:v:0", "0:a:1", "0:a:2", "0:a:0", "0:s:0", "0:s:1"]


def test_opus_gets_a_layout_the_encoder_accepts(config):
    # ffmpeg's libopus rejects 5.1(side), which DTS rips carry.
    plan = build_plan(config, info_for({3: {"channel_layout": "5.1(side)"}}), "/out/movie.mkv")
    decision = [d for d in plan.audio if d.source_index == 3][0]
    assert decision.layout == "5.1"
    assert "-channel_layout:a:%d 5.1" % decision.out_index in " ".join(plan.command(0))


def test_a_supported_layout_needs_no_change(config):
    plan = build_plan(config, info_for({3: {"channel_layout": "5.1"}}), "/out/movie.mkv")
    decision = [d for d in plan.audio if d.source_index == 3][0]
    assert decision.layout == ""


def test_aac_keeps_the_source_layout(config):
    config.audio.codec = "aac"
    plan = build_plan(config, info_for({3: {"channel_layout": "5.1(side)"}}), "/out/movie.mkv")
    assert all(decision.layout == "" for decision in plan.audio)


def test_seven_point_one_downmixes_to_six_channels(config):
    plan = build_plan(config, info_for(), "/out/movie.mkv")
    encoded = [d for d in plan.audio if d.action == "encode"]
    assert encoded[0].downmix_to == 6
    assert "-ac:a:0 6" in " ".join(plan.command(0))


def test_copy_policy_copies_every_audio_track(config):
    config.audio.policy = "copy"
    plan = build_plan(config, info_for(), "/out/movie.mkv")
    assert all(d.action == "copy" for d in plan.audio)
    assert "-ac:a:0" not in " ".join(plan.command(0))


def test_audio_keep_drops_other_languages(config):
    config.language.audio_keep = ["eng"]
    plan = build_plan(config, info_for(), "/out/movie.mkv")
    assert [d.language for d in plan.audio] == ["eng", "eng"]


def test_subtitle_default_is_the_first_preferred_track(config):
    plan = build_plan(config, info_for(), "/out/movie.mkv")
    assert [d.language for d in plan.subtitles] == ["spa", "eng"]
    assert plan.subtitles[1].default is True
    assert "-disposition:s:1 default" in " ".join(plan.command(0))


def test_image_subtitles_can_be_dropped(config):
    config.subtitles.keep_image_tracks = False
    plan = build_plan(config, info_for(), "/out/movie.mkv")
    assert [d.language for d in plan.subtitles] == ["spa"]


def test_loudnorm_adds_an_audio_filter(config):
    config.audio.loudnorm = True
    plan = build_plan(config, info_for(), "/out/movie.mkv")
    assert "-af:a:0 loudnorm=I=-23:TP=-2:LRA=11" in " ".join(plan.command(0))


def test_commentary_tracks_can_be_dropped(config):
    before = len(build_plan(config, info_for(), "/out/movie.mkv").audio)
    config.audio.keep_commentary = False
    plan = build_plan(config, info_for(), "/out/movie.mkv")
    assert len(plan.audio) == before - 1


def test_forced_only_keeps_forced_subtitles(config):
    config.subtitles.forced_only = True
    plan = build_plan(config, info_for({5: {"disposition": {"forced": 1}}}), "/out/movie.mkv")
    assert [d.source_index for d in plan.subtitles] == [5]


def test_subtitle_default_track_none_clears_the_flag(config):
    config.subtitles.default_track = "none"
    plan = build_plan(config, info_for(), "/out/movie.mkv")
    assert not any(d.default for d in plan.subtitles)
    assert "-disposition:s:" not in " ".join(plan.command(0))


def test_subtitle_default_track_source_uses_the_source_flag(config):
    config.subtitles.default_track = "source"
    plan = build_plan(config, info_for({4: {"disposition": {"default": 1}}}), "/out/movie.mkv")
    assert plan.subtitles[0].default is True
    assert plan.subtitles[1].default is False


def test_audio_extra_args_reach_the_command(config):
    config.audio.extra_args = ["compression_level=10"]
    plan = build_plan(config, info_for(), "/out/movie.mkv")
    assert "-compression_level 10" in " ".join(plan.command(0))


def test_deinterlace_filter_appears_for_an_interlaced_source(config):
    plan = build_plan(config, info_for({0: {"field_order": "tt"}}), "/out/movie.mkv")
    assert any("bwdif" in part for part in plan.filters)
    assert "-vf" in plan.command(0)


def test_deinterlace_never_removes_the_filter(config):
    config.video.deinterlace = "never"
    plan = build_plan(config, info_for({0: {"field_order": "tt"}}), "/out/movie.mkv")
    assert not any("bwdif" in part for part in plan.filters)


def test_scale_filter_when_max_width_is_smaller(config):
    config.video.max_width = 1280
    plan = build_plan(config, info_for(), "/out/movie.mkv")
    assert any(part.startswith("scale=w=1280") for part in plan.filters)


def test_scale_filter_absent_when_source_is_already_small(config):
    config.video.max_width = 1920
    plan = build_plan(config, info_for(), "/out/movie.mkv")
    assert not any(part.startswith("scale") for part in plan.filters)


def test_two_pass_mode_sets_a_video_bitrate(config):
    config.video.target_size_gb = 1.0
    plan = build_plan(config, info_for(), "/out/movie.mkv")
    assert plan.passes == 2
    assert 0 < plan.video_bitrate < 14000000
    first = plan.command(0)
    second = plan.command(1)
    assert "-pass" in first and first[-3:] == ["-f", "null", "/dev/null"]
    assert "-pass" in second
    assert "-b:v" in first
    assert first[first.index("-pass") + 1] == "1"
    assert second[second.index("-pass") + 1] == "2"


def test_crf_mode_is_single_pass(config):
    plan = build_plan(config, info_for(), "/out/movie.mkv")
    assert plan.passes == 1
    assert "-crf" in plan.command(0)


def test_hevc_profile_selects_x265_and_its_preset(config):
    config.video.profile = "hevc"
    plan = build_plan(config, info_for(), "/out/movie.mkv")
    command = " ".join(plan.command(0))
    assert "-c:v:0 libx265" in command
    assert "-preset slow" in command
    assert "-x265-params log-level=none" in command


def test_copy_profile_leaves_the_video_alone(config):
    config.video.profile = "copy"
    plan = build_plan(config, info_for(), "/out/movie.mkv")
    command = " ".join(plan.command(0))
    assert "-c:v:0 copy" in command
    assert "-crf" not in command
    assert "-pix_fmt" not in command


def test_quality_args_replace_crf_for_hardware_encoders(config):
    config.video.av1.encoder = "av1_nvenc"
    config.video.av1.preset = "p6"
    config.video.av1.quality_args = ["-rc", "QVBR", "-cq", "30"]
    plan = build_plan(config, info_for(), "/out/movie.mkv")
    command = " ".join(plan.command(0))
    assert "-rc QVBR -cq 30" in command
    assert "-crf" not in command
    assert "-preset p6" in command


def test_tracks_without_a_language_get_the_default_language_tag(config):
    plan = build_plan(config, info_for({1: {"tags": {}}}), "/out/movie.mkv")
    command = " ".join(plan.command(0))
    # The track without a language lands last after the language reorder.
    assert plan.audio[2].language is None
    assert "-metadata:s:a:2 language=eng" in command


def test_chapters_and_metadata_are_mapped(config):
    data = dict(SAMPLE_JSON, chapters=[{"start": 0, "end": 10}])
    plan = build_plan(config, probe.parse(data, "/in/movie.mkv"), "/out/movie.mkv",
                      title_tag="Alien (1979)", imdb_id="tt0078748")
    command = " ".join(plan.command(0))
    assert "-map_chapters 0" in command
    assert "-map_metadata 0" in command
    assert "IMDB=tt0078748" in command


def test_matroska_options_follow_the_format_flag(config):
    command = build_plan(config, info_for(), "/out/movie.mkv").command(0)
    assert command[-1] == "/out/movie.mkv"
    format_index = command.index("-f")
    assert command[format_index + 1] == "matroska"
    assert command.index("-progress") < format_index


@needs_ffmpeg
def test_plan_runs_on_a_real_file(config, sample):
    plan = build_plan(config, probe.ffprobe(sample), "/out/movie.mkv")
    assert plan.video.action == "encode"
    assert len(plan.audio) == 3
    assert len(plan.subtitles) == 2
