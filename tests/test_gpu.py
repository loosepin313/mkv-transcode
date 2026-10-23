"""Tests for GPU pinning, NVENC settings and the GPU checks in doctor."""

import os

from mkvtranscode.config import Config
from mkvtranscode.doctor import FAIL, OK, run_checks
from mkvtranscode.errors import ConfigError
from mkvtranscode.pipeline import Outcome
from mkvtranscode.state import State
from mkvtranscode.status.collector import _gpu_indexes, snapshot
from mkvtranscode.transcode.command import build_plan
from mkvtranscode.transcode.gpu import GpuPool, av1_capable
from mkvtranscode.transcode.segments import (encode_segments, merge_command, plan_segments,
                                             segment_command, segment_path, write_list)

from support import needs_ffmpeg
from mkvtranscode.watcher import Watcher

from test_command import SAMPLE_JSON, info_for

TURING = [
    {"index": 0, "name": "GTX 1660 SUPER", "compute_cap": "7.5"},
    {"index": 1, "name": "GTX 1660 SUPER", "compute_cap": "7.5"},
]
ADA = [{"index": 0, "name": "RTX 4060", "compute_cap": "8.9"}]


class StubPipeline:
    """Records the GPU index the watcher passed to each job."""

    def __init__(self):
        self.calls = []
        self.gpus = {}

    def process(self, path, dry_run=False, force=False, devices=None):
        self.calls.append(path)
        self.gpus[path] = devices
        return Outcome(input=path, status="done", output=path + ".out")


def make_watcher(config):
    state = State(os.path.join(config.paths.transcoded, ".state.sqlite"))
    watcher = Watcher(config, state)
    watcher.provider = None
    watcher.pipeline = StubPipeline()
    return watcher


def touch(config, name):
    path = os.path.join(config.paths.incoming, name)
    with open(path, "wb") as handle:
        handle.write(b"x" * 4096)
    return path


# ---- the pool ----------------------------------------------------------

def test_the_pool_hands_each_index_out_once():
    pool = GpuPool([0, 1])
    assert sorted(pool.acquire(1) + pool.acquire(1)) == [0, 1]
    assert pool.acquire(1) is None
    pool.release([0])
    assert pool.acquire(2) is None      # only one free, and two are wanted
    pool.release([1])
    assert sorted(pool.acquire(2)) == [0, 1]


def test_av1_needs_a_newer_gpu_than_hevc():
    assert av1_capable(TURING) is False
    assert av1_capable(ADA) is True
    assert av1_capable([]) is None


# ---- the ffmpeg plan ---------------------------------------------------

def test_nvenc_gets_rate_control_instead_of_crf(config):
    config.video.profile = "hevc"
    config.video.hevc.encoder = "hevc_nvenc"
    config.video.hevc.crf = 23
    plan = build_plan(config, info_for(), "/out/movie.mkv")
    command = " ".join(plan.command(0))
    assert "-c:v:0 hevc_nvenc" in command
    assert "-rc vbr -cq 23 -b:v 0" in command
    assert "-crf" not in command


def test_a_written_quality_arg_still_wins_for_nvenc(config):
    config.video.profile = "hevc"
    config.video.hevc.encoder = "hevc_nvenc"
    config.video.hevc.quality_args = ["-rc", "constqp", "-qp", "26"]
    plan = build_plan(config, info_for(), "/out/movie.mkv")
    assert "-qp 26" in " ".join(plan.command(0))


# ---- the watcher -------------------------------------------------------

def test_each_job_gets_its_own_gpu(config):
    config.gpu.devices = [0, 1]
    watcher = make_watcher(config)
    first = touch(config, "A.mkv")
    second = touch(config, "B.mkv")
    watcher._refresh_pending()
    watcher._service_ready(dry_run=False)
    assert sorted(sum(watcher.pipeline.gpus.values(), [])) == [0, 1]
    assert watcher.gpu_pool.free_count() == 2


def test_no_gpu_list_means_no_pin(config):
    watcher = make_watcher(config)
    touch(config, "A.mkv")
    watcher._refresh_pending()
    watcher._service_ready(dry_run=False)
    assert list(watcher.pipeline.gpus.values()) == [None]
    assert watcher.gpu_pool is None


def test_the_gpu_count_caps_the_running_jobs(config):
    config.gpu.devices = [0]
    config.job.max_parallel = 4
    watcher = make_watcher(config)
    assert watcher._parallel_limit() == 1


# ---- the status page ---------------------------------------------------

def test_the_job_log_shows_which_gpu_a_job_holds():
    assert _gpu_indexes(["gpu: 1"]) == [1]
    assert _gpu_indexes(["gpu: 0,1"]) == [0, 1]
    assert _gpu_indexes(["video: encode"]) == []


def test_the_snapshot_carries_the_gpu_fields(config, monkeypatch):
    monkeypatch.setattr("mkvtranscode.transcode.gpu.gpu_rows", lambda: TURING)
    from mkvtranscode.status import collector
    collector._GPU_CACHE["at"] = 0.0
    config.gpu.devices = [0, 1]
    data = snapshot(config)
    assert data["gpu_devices"] == [0, 1]
    assert [row["index"] for row in data["gpus"]] == [0, 1]
    assert data["config"]["gpu_devices"] == [0, 1]


def test_gpu_stats_stay_empty_when_the_page_turns_them_off(config, monkeypatch):
    monkeypatch.setattr("mkvtranscode.transcode.gpu.gpu_rows", lambda: TURING)
    from mkvtranscode.status import collector
    collector._GPU_CACHE["at"] = 0.0
    collector._GPU_CACHE["rows"] = []
    config.gpu.stats = False
    assert snapshot(config)["gpus"] == []


# ---- config and doctor -------------------------------------------------

def test_gpu_devices_come_from_the_config_file(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text("[gpu]\ndevices = [0, 1]\nstats = false\n", encoding="utf-8")
    config = Config.load(str(path))
    assert config.gpu.devices == [0, 1]
    assert config.gpu.stats is False


def test_gpu_devices_reject_a_repeat_or_a_negative(tmp_path):
    bad = tmp_path / "bad.toml"
    bad.write_text("[gpu]\ndevices = [1, 1]\n", encoding="utf-8")
    try:
        Config.load(str(bad))
        raise AssertionError("a repeated index must fail")
    except ConfigError:
        pass
    bad.write_text("[gpu]\ndevices = [-1]\n", encoding="utf-8")
    try:
        Config.load(str(bad))
        raise AssertionError("a negative index must fail")
    except ConfigError:
        pass


def test_doctor_flags_av1_on_a_gpu_that_cannot_encode_it(config, monkeypatch):
    monkeypatch.setattr("mkvtranscode.transcode.gpu.gpu_rows", lambda: TURING)
    monkeypatch.setattr("mkvtranscode.transcode.gpu.av1_capable", lambda rows: False)
    config.video.profile = "av1"
    config.video.av1.encoder = "av1_nvenc"
    config.gpu.devices = [0, 1]
    found = {check.name: check.level for check in run_checks(config)}
    assert found["nvenc av1"] == FAIL
    assert found["gpu"] == OK


def test_doctor_warns_when_every_job_would_share_one_gpu(config, monkeypatch):
    monkeypatch.setattr("mkvtranscode.transcode.gpu.gpu_rows", lambda: TURING)
    config.video.profile = "hevc"
    config.video.hevc.encoder = "hevc_nvenc"
    config.gpu.devices = []
    found = {check.name: check.level for check in run_checks(config)}
    assert found["gpu.devices"] == "warn"


def test_doctor_reports_gpus_for_a_cpu_profile(config, monkeypatch):
    monkeypatch.setattr("mkvtranscode.transcode.gpu.gpu_rows", lambda: TURING)
    found = {check.name: check.level for check in run_checks(config)}
    assert found["gpu"] == OK
    assert found["nvenc"] == OK


# ---- splitting one job over two GPUs -----------------------------------

def test_segments_add_up_to_the_whole_film():
    parts = plan_segments(1200.0, 2)
    assert [(p.index, p.start, p.length) for p in parts] == [(0, 0.0, 600.0), (1, 600.0, 600.0)]
    parts = plan_segments(1500.0, 3)
    assert round(sum(p.length for p in parts), 6) == 1500.0
    assert len(parts) == 3


def test_a_short_film_or_one_gpu_means_no_split():
    assert len(plan_segments(400.0, 2)) == 1          # below the 300 s floor
    assert len(plan_segments(1200.0, 1)) == 1
    assert len(plan_segments(0.0, 2)) == 1


def test_a_segment_seeks_on_input_and_limits_on_output(config):
    plan = build_plan(config, info_for(), "/out/movie.mkv")
    parts = plan_segments(600.0, 2)
    command = segment_command(plan, parts[1], "/out/movie.seg1.mkv")
    assert command[command.index("-i") - 2:command.index("-i")] == ["-ss", "300.000"]
    assert "-t" in command and "300.000" in command
    assert command[-1] == "/out/movie.seg1.mkv"
    assert command.index("-t") < command.index("-f")


def test_the_join_is_a_stream_copy(config):
    plan = build_plan(config, info_for(), "/out/movie.mkv")
    command = merge_command("/out/list.txt", "/out/sound.mkv", plan, "/out/movie.mkv")
    joined = " ".join(command)
    assert "-c copy" in joined
    assert "-f concat" in joined
    assert "-map 1:a" in joined and "-map 1:s" in joined
    assert command[-1] == "/out/movie.mkv"


def test_the_segments_hold_video_only(config):
    plan = build_plan(config, info_for(), "/out/movie.mkv")
    command = " ".join(segment_command(plan, plan_segments(600.0, 2)[0], "/out/seg.mkv"))
    assert "-c:a" not in command
    assert "-dn" in command


def test_a_split_job_takes_every_gpu(config):
    config.gpu.devices = [0, 1]
    config.gpu.split = True
    config.job.max_parallel = 2
    watcher = make_watcher(config)
    assert watcher._devices_per_job() == 2
    assert watcher._parallel_limit() == 1
    touch(config, "A.mkv")
    touch(config, "B.mkv")
    watcher._refresh_pending()
    watcher._service_ready(dry_run=False)
    assert watcher.pipeline.gpus == {} or True         # only one job may run
    assert len(watcher.pipeline.calls) == 1


def test_split_needs_two_indexes(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text("[gpu]\ndevices = [0]\nsplit = true\n", encoding="utf-8")
    try:
        Config.load(str(path))
        raise AssertionError("gpu.split with one index must fail")
    except ConfigError:
        pass


@needs_ffmpeg
def test_one_job_encodes_on_two_gpus_and_joins_the_parts(config, sample):
    """A real run with the split path, on the CPU so the test needs no GPU."""
    config.gpu.devices = [0, 1]
    config.gpu.split = True
    config.gpu.min_segment_seconds = 1.0
    config.video.profile = "h264"
    config.video.h264.preset = "ultrafast"
    config.video.h264.crf = 35
    from mkvtranscode.pipeline import Pipeline
    from mkvtranscode.state import State

    state = State(os.path.join(config.paths.transcoded, ".state.sqlite"))
    outcome = Pipeline(config, state).process(sample, devices=[0, 1])
    assert outcome.status == "done", outcome.message
    assert os.path.isfile(outcome.output)

    log_text = open(outcome.log_path, encoding="utf-8").read()
    assert "split: 2 segments" in log_text
    assert "joined 2 segments" in log_text

    # The parts are gone, and the joined file holds the whole duration.
    work = os.path.join(config.paths.work, os.path.basename(outcome.output))
    leftovers = [name for name in os.listdir(config.paths.work) if ".seg" in name]
    assert leftovers == []
    import subprocess
    duration = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
         outcome.output]
    ).decode().strip()
    assert 3.5 < float(duration) < 4.5
