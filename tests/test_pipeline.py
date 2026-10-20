"""End-to-end tests for the pipeline.

These tests run ffmpeg. They skip when ffmpeg is absent.
"""

import json
import os

from mkvtranscode.pipeline import Pipeline
from mkvtranscode.probe import ffprobe
from mkvtranscode.state import DONE, FAILED, SKIPPED, State

from support import needs_ffmpeg


def out_path(config, title, year, name=None):
    folder = "%s (%s)" % (title, year)
    return os.path.join(config.paths.transcoded, folder, (name or folder) + ".mkv")


@needs_ffmpeg
def test_process_produces_the_documented_layout(config, sample):
    state = State(os.path.join(config.paths.transcoded, ".state.sqlite"))
    outcome = Pipeline(config, state).process(sample)
    state.close()

    assert outcome.status == DONE
    target = out_path(config, "Alien", 1979)
    assert outcome.output == target
    assert os.path.isfile(target)
    assert not os.listdir(config.paths.work)

    info = ffprobe(target)
    assert info.primary_video().codec_name == "av1"
    assert info.bit_depth() == 10
    assert len(info.audios()) == 3
    codecs = [a.codec_name for a in info.audios()]
    assert codecs == ["ac3", "libopus", "aac"] or codecs == ["ac3", "opus", "aac"]
    assert info.audios()[0].language == "eng"
    assert info.audios()[0].default is True
    assert len(info.subtitles()) == 2
    assert info.subtitles()[0].language == "eng"
    assert info.subtitles()[0].default is True


@needs_ffmpeg
def test_second_run_skips_the_same_file(config, sample):
    state = State(os.path.join(config.paths.transcoded, ".state.sqlite"))
    pipeline = Pipeline(config, state)
    first = pipeline.process(sample)
    second = pipeline.process(sample)
    state.close()
    assert first.status == DONE
    assert second.status == SKIPPED


@needs_ffmpeg
def test_dry_run_changes_nothing(config, sample):
    state = State(os.path.join(config.paths.transcoded, ".state.sqlite"))
    outcome = Pipeline(config, state).process(sample, dry_run=True)
    state.close()
    assert outcome.status == "planned"
    assert "ffmpeg" in outcome.message
    assert not os.path.isdir(os.path.dirname(out_path(config, "Alien", 1979)))


@needs_ffmpeg
def test_interlaced_source_comes_back_progressive(config, interlaced_sample):
    source = ffprobe(interlaced_sample)
    assert source.is_interlaced()

    state = State(os.path.join(config.paths.transcoded, ".state.sqlite"))
    outcome = Pipeline(config, state).process(interlaced_sample)
    state.close()

    assert outcome.status == DONE
    result = ffprobe(outcome.output)
    assert result.primary_video().field_order == "progressive"


@needs_ffmpeg
def test_hevc_profile_produces_hevc(config, sample):
    config.video.profile = "hevc"
    config.video.hevc.preset = "ultrafast"
    config.video.hevc.crf = 32
    state = State(os.path.join(config.paths.transcoded, ".state.sqlite"))
    outcome = Pipeline(config, state).process(sample)
    state.close()
    assert outcome.status == DONE
    assert ffprobe(outcome.output).primary_video().codec_name == "hevc"


@needs_ffmpeg
def test_truncated_source_fails_the_output_check(config, sample):
    broken = os.path.join(config.paths.incoming, "Broken.1979.mkv")
    size = os.path.getsize(sample)
    with open(sample, "rb") as source, open(broken, "wb") as target:
        target.write(source.read(int(size * 0.4)))

    state = State(os.path.join(config.paths.transcoded, ".state.sqlite"))
    outcome = Pipeline(config, state).process(broken)
    state.close()
    assert outcome.status == FAILED
    assert "duration" in outcome.message or "output check" in outcome.message


@needs_ffmpeg
def test_report_and_log_files_are_written(config, sample):
    state = State(os.path.join(config.paths.transcoded, ".state.sqlite"))
    outcome = Pipeline(config, state).process(sample)
    state.close()
    assert os.path.isfile(outcome.log_path)
    assert os.path.isfile(outcome.report_path)
    with open(outcome.report_path, encoding="utf-8") as handle:
        report = json.load(handle)
    assert report["status"] == DONE
    assert report["title"] == "Alien"
    assert report["output_size"] > 0


@needs_ffmpeg
def test_move_source_to_adds_a_sub_folder_inside_processed(config, sample):
    config.output.move_source_to = "processed"
    state = State(os.path.join(config.paths.transcoded, ".state.sqlite"))
    outcome = Pipeline(config, state).process(sample)
    state.close()
    assert outcome.status == DONE
    assert not os.path.exists(sample)
    moved = os.path.join(config.paths.processed, "processed")
    assert os.listdir(moved) == ["Alien.1979.1080p.BluRay.DTS-HD.MA.5.1.mkv"]


@needs_ffmpeg
def test_the_default_moves_the_source_to_processed(config, sample):
    state = State(os.path.join(config.paths.transcoded, ".state.sqlite"))
    outcome = Pipeline(config, state).process(sample)
    state.close()
    assert outcome.status == DONE
    assert not os.path.exists(sample)
    assert os.listdir(config.paths.processed) == ["Alien.1979.1080p.BluRay.DTS-HD.MA.5.1.mkv"]


@needs_ffmpeg
def test_source_action_keep_leaves_the_source_in_incoming(config, sample):
    config.output.source_action = "keep"
    state = State(os.path.join(config.paths.transcoded, ".state.sqlite"))
    outcome = Pipeline(config, state).process(sample)
    state.close()
    assert outcome.status == DONE
    assert os.path.exists(sample)


@needs_ffmpeg
def test_delete_source_removes_the_input(config, sample):
    config.output.delete_source = True
    state = State(os.path.join(config.paths.transcoded, ".state.sqlite"))
    outcome = Pipeline(config, state).process(sample)
    state.close()
    assert outcome.status == DONE
    assert not os.path.exists(sample)


@needs_ffmpeg
def test_imdb_match_writes_an_nfo_sidecar(config, sample, imdb_index):
    renamed = os.path.join(config.paths.incoming, "Alien.1979.1080p.BluRay.tt0078748.mkv")
    os.rename(sample, renamed)
    config.metadata.provider = "imdb"

    state = State(os.path.join(config.paths.transcoded, ".state.sqlite"))
    pipeline = Pipeline(config, state, imdb_index)
    outcome = pipeline.process(renamed)
    state.close()

    assert outcome.status == DONE
    nfo = os.path.splitext(outcome.output)[0] + ".nfo"
    assert os.path.isfile(nfo)
    text = open(nfo, encoding="utf-8").read()
    assert "<imdbid>tt0078748</imdbid>" in text
    assert "<year>1979</year>" in text


@needs_ffmpeg
def test_two_pass_hits_the_target_size(config, sample):
    config.video.target_size_gb = 0.002
    state = State(os.path.join(config.paths.transcoded, ".state.sqlite"))
    outcome = Pipeline(config, state).process(sample)
    state.close()
    assert outcome.status == DONE
    size = os.path.getsize(outcome.output)
    assert 1000000 < size < 4000000


def test_process_skips_a_symlink_and_keeps_the_target(config):
    target = os.path.join(config.paths.processed, "keep.mkv")
    with open(target, "wb") as handle:
        handle.write(b"x" * 4096)
    link = os.path.join(config.paths.incoming, "Link.mkv")
    os.symlink(target, link)

    outcome = Pipeline(config).process(link)

    assert outcome.status == SKIPPED
    assert os.path.isfile(target)


@needs_ffmpeg
def test_job_log_and_report_are_owner_only(config, sample):
    outcome = Pipeline(config).process(sample)

    assert outcome.status == DONE
    for path in (outcome.log_path, outcome.report_path):
        assert os.stat(path).st_mode & 0o777 == 0o600
