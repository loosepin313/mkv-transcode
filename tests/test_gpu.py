"""Tests for GPU pinning, NVENC settings and the GPU checks in doctor."""

import os

from mkvtranscode.config import Config
from mkvtranscode.doctor import FAIL, OK, run_checks
from mkvtranscode.errors import ConfigError
from mkvtranscode.pipeline import Outcome
from mkvtranscode.state import State
from mkvtranscode.status.collector import _gpu_index, snapshot
from mkvtranscode.transcode.command import build_plan
from mkvtranscode.transcode.gpu import GpuPool, av1_capable
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

    def process(self, path, dry_run=False, force=False, gpu=None):
        self.calls.append(path)
        self.gpus[path] = gpu
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
    assert sorted([pool.acquire(), pool.acquire()]) == [0, 1]
    assert pool.acquire() is None
    pool.release(0)
    assert pool.acquire() == 0
    assert pool.free_count() == 0


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
    assert sorted(watcher.pipeline.gpus.values()) == [0, 1]
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
    assert _gpu_index(["gpu: 1"]) == 1
    assert _gpu_index(["video: encode"]) is None


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
