"""Tests for the incoming folder scanner."""

import os
import time

from mkvtranscode.scan import acceptable, is_ready, list_candidates, open_by


def write(path, size_kb=200, content=b"x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(content * (size_kb * 1024))
    return path


def test_acceptable_applies_extension_and_prefix_rules(config):
    assert acceptable("/in/Alien.1979.mkv", config)
    assert not acceptable("/in/Alien.1979.mp4", config)
    assert not acceptable("/in/.Alien.mkv", config)
    assert not acceptable("/in/_Alien.mkv", config)
    assert not acceptable("/in/sample.mkv", config)
    assert not acceptable("/in/Alien.mkv.part", config)
    assert not acceptable("/in/Alien.mkv.crdownload", config)


def test_extension_list_is_configurable(config):
    config.watch.extensions = ["mkv", "mp4"]
    config.resolve()
    assert acceptable("/in/Alien.mp4", config)


def test_list_candidates_skips_small_files_and_sorts_by_age(config):
    old = write(os.path.join(config.paths.incoming, "Old.mkv"), size_kb=400)
    write(os.path.join(config.paths.incoming, "New.mkv"), size_kb=400)
    os.utime(old, (time.time() - 100, time.time() - 100))
    config.watch.min_size_mb = 0.1
    found = list_candidates(config)
    assert [os.path.basename(c.path) for c in found] == ["Old.mkv", "New.mkv"]
    assert found[0].size == 400 * 1024


def test_list_candidates_ignores_a_missing_incoming(config):
    config.paths.incoming = os.path.join(config.paths.incoming, "nope")
    assert list_candidates(config) == []


def test_is_ready_waits_for_stable_seconds(config):
    path = write(os.path.join(config.paths.incoming, "Fresh.mkv"), size_kb=400)
    config.watch.stable_seconds = 5
    assert is_ready(path, config) is False
    config.watch.stable_seconds = 0
    assert is_ready(path, config) is True


def test_is_ready_false_while_the_size_changes(config):
    from mkvtranscode.scan import Candidate

    path = write(os.path.join(config.paths.incoming, "Growing.mkv"), size_kb=400)
    previous = Candidate(path=path, size=1, mtime=0.0, observed_at=time.time() - 60)
    assert is_ready(path, config, previous=previous) is False


def test_is_ready_true_after_the_size_stops_changing(config):
    from mkvtranscode.scan import Candidate

    path = write(os.path.join(config.paths.incoming, "Stable.mkv"), size_kb=400)
    stat = os.stat(path)
    previous = Candidate(path=path, size=stat.st_size, mtime=stat.st_mtime,
                         observed_at=time.time() - 60)
    assert is_ready(path, config, previous=previous) is True


def test_force_drops_the_stability_wait_only(config):
    """A forced pass starts a settled file at once, but never a growing one."""
    from mkvtranscode.scan import Candidate

    config.watch.stable_seconds = 600
    path = write(os.path.join(config.paths.incoming, "Settled.mkv"), size_kb=400)
    stat = os.stat(path)
    fresh = Candidate(path=path, size=stat.st_size, mtime=stat.st_mtime,
                      observed_at=time.time())
    assert is_ready(path, config, previous=fresh) is False
    assert is_ready(path, config, previous=fresh, force=True) is True

    growing = Candidate(path=path, size=1, mtime=0.0, observed_at=0.0)
    assert is_ready(path, config, previous=growing, force=True) is False


def test_min_size_bytes_overrides_the_floor_for_one_pass(config):
    write(os.path.join(config.paths.incoming, "Clip.mkv"), size_kb=10)
    config.watch.min_size_mb = 1
    assert list_candidates(config) == []
    assert [c.path for c in list_candidates(config, min_size_bytes=0)]


def test_open_by_finds_a_process_holding_the_file(config):
    import subprocess

    path = write(os.path.join(config.paths.incoming, "Held.mkv"), size_kb=400)
    handle = open(path, "rb")
    child = subprocess.Popen(["sleep", "5"], stdin=handle)
    handle.close()
    try:
        holder = open_by(path)
        assert holder and str(child.pid) in holder
    finally:
        child.terminate()
        child.wait()


def test_open_by_ignores_our_own_process(config):
    path = write(os.path.join(config.paths.incoming, "Ours.mkv"), size_kb=400)
    with open(path, "rb"):
        assert open_by(path) is None


def test_follow_subdirs_finds_nested_files(config):
    config.watch.follow_subdirs = True
    write(os.path.join(config.paths.incoming, "disc2", "Deep.mkv"), size_kb=400)
    config.watch.min_size_mb = 0.1
    names = [os.path.basename(c.path) for c in list_candidates(config)]
    assert "Deep.mkv" in names


def test_list_candidates_skips_a_symlink_to_a_file_outside_incoming(config, tmp_path):
    outside = write(str(tmp_path / "outside.mkv"), size_kb=400)
    os.symlink(outside, os.path.join(config.paths.incoming, "Alien.mkv"))
    config.watch.min_size_mb = 0.1
    assert list_candidates(config) == []


def test_list_candidates_skips_a_symlinked_directory(config, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    write(str(other / "Hidden.mkv"), size_kb=400)
    os.symlink(str(other), os.path.join(config.paths.incoming, "linkdir"))
    config.watch.follow_subdirs = True
    config.watch.min_size_mb = 0.1
    assert list_candidates(config) == []
