"""Tests for the watcher loop.

The pipeline is replaced with a stub, so these tests run without ffmpeg.
"""

import os
import time

from mkvtranscode.pipeline import Outcome
from mkvtranscode.scan import Candidate
from mkvtranscode.state import State
from mkvtranscode.watcher import Watcher


class StubPipeline:
    """Records what the watcher asked it to process."""

    def __init__(self, status="done"):
        self.status = status
        self.calls = []

    def process(self, path, dry_run=False, force=False, gpu=None):
        self.calls.append(path)
        self.gpus = getattr(self, "gpus", {})
        self.gpus[path] = gpu
        return Outcome(input=path, status=self.status, output=path + ".out")


def make_watcher(config, status="done"):
    state = State(os.path.join(config.paths.transcoded, ".state.sqlite"))
    watcher = Watcher(config, state)
    watcher.provider = None
    watcher.pipeline = StubPipeline(status)
    return watcher


def touch(config, name, size=400 * 1024):
    path = os.path.join(config.paths.incoming, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(b"x" * size)
    return path


def test_ready_file_starts_one_job(config):
    watcher = make_watcher(config)
    path = touch(config, "Ready.mkv")
    watcher._refresh_pending()
    watcher._service_ready(dry_run=False)
    assert watcher.pipeline.calls == [path]
    assert watcher.pending == {}


def test_a_file_that_is_still_growing_stays_pending(config):
    watcher = make_watcher(config)
    path = touch(config, "Growing.mkv")
    watcher.pending[path] = Candidate(path=path, size=1, mtime=0.0, observed_at=0.0)
    watcher._service_ready(dry_run=False)
    assert watcher.pipeline.calls == []
    assert path in watcher.pending
    assert watcher.pending[path].size == os.path.getsize(path)


def test_stability_window_delays_the_start(config):
    config.watch.stable_seconds = 30
    watcher = make_watcher(config)
    path = touch(config, "Fresh.mkv")
    stat = os.stat(path)
    watcher.pending[path] = Candidate(path=path, size=stat.st_size, mtime=stat.st_mtime,
                                      observed_at=time.time())
    watcher._service_ready(dry_run=False)
    assert watcher.pipeline.calls == []

    watcher.pending[path] = Candidate(path=path, size=stat.st_size, mtime=stat.st_mtime,
                                      observed_at=time.time() - 60)
    watcher._service_ready(dry_run=False)
    assert watcher.pipeline.calls == [path]


def test_finished_files_are_not_queued_again(config):
    watcher = make_watcher(config)
    path = touch(config, "Once.mkv")
    watcher._refresh_pending()
    watcher._service_ready(dry_run=False)
    watcher._refresh_pending()
    watcher._service_ready(dry_run=False)
    assert watcher.pipeline.calls == [path]


def test_failed_files_stop_after_max_attempts(config):
    config.job.max_attempts = 2
    watcher = make_watcher(config, status="failed")
    path = touch(config, "Broken.mkv")
    for _ in range(5):
        watcher._refresh_pending()
        watcher._service_ready(dry_run=False)
    assert len(watcher.pipeline.calls) == 2
    assert path in watcher.finished


def test_max_parallel_one_processes_inline(config):
    watcher = make_watcher(config)
    touch(config, "A.mkv")
    touch(config, "B.mkv")
    watcher._refresh_pending()
    watcher._service_ready(dry_run=False)
    assert len(watcher.pipeline.calls) == 2
    assert getattr(watcher, "_executor", None) is None


def test_non_media_files_are_ignored(config):
    watcher = make_watcher(config)
    touch(config, "notes.txt")
    touch(config, ".hidden.mkv")
    watcher._refresh_pending()
    assert watcher.pending == {}


class StubProvider:
    def status(self):
        return "stub"


def test_once_gives_up_on_a_file_that_never_settles(config, monkeypatch):
    import subprocess

    config.watch.mode = "poll"
    config.watch.poll_interval = 0.5
    config.watch.stable_seconds = 0.0
    config.watch.check_open_files = True

    path = touch(config, "Held.mkv")
    handle = open(path, "rb")
    child = subprocess.Popen(["sleep", "10"], stdin=handle)
    handle.close()

    watcher = make_watcher(config)
    watcher.provider = StubProvider()
    try:
        code = watcher.run(once=True, wait_seconds=1.0)
    finally:
        child.terminate()
        child.wait()

    assert code == 1
    assert watcher.pipeline.calls == []


def test_once_exits_zero_when_nothing_is_pending(config):
    watcher = make_watcher(config)
    watcher.provider = StubProvider()
    assert watcher.run(once=True, wait_seconds=1.0) == 0
