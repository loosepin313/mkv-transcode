"""Tests for the file helpers in util.py."""

import errno
import os

from mkvtranscode import util


def write_file(path, size_kb=8):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(bytes(range(256)) * (size_kb * 4))
    return path


def test_move_inside_one_filesystem_renames(tmp_path):
    src = write_file(str(tmp_path / "work" / "a.mkv"))
    dst = str(tmp_path / "library" / "a.mkv")
    assert util.cross_device_move(src, dst) is False
    assert os.path.isfile(dst)
    assert not os.path.exists(src)


def test_move_across_filesystems_copies(tmp_path, monkeypatch):
    payload = bytes(range(256)) * 32
    src = str(tmp_path / "work" / "b.mkv")
    dst = str(tmp_path / "library" / "b.mkv")
    write_file(src)
    with open(src, "wb") as handle:
        handle.write(payload)
    real_replace = os.replace

    def fake_replace(old, new):
        # Only the first hop fails, the way a real EXDEV error does.
        if old == src:
            raise OSError(errno.EXDEV, "Invalid cross-device link")
        return real_replace(old, new)

    monkeypatch.setattr(util.os, "replace", fake_replace)
    assert util.cross_device_move(src, dst) is True
    with open(dst, "rb") as handle:
        written = handle.read()
    assert written == payload
    assert not os.path.exists(src)
    assert not os.path.exists(dst + ".part")


def test_move_reports_other_errors(tmp_path, monkeypatch):
    src = write_file(str(tmp_path / "work" / "c.mkv"))
    dst = str(tmp_path / "library" / "c.mkv")

    def fake_replace(old, new):
        raise OSError(errno.EACCES, "Permission denied")

    monkeypatch.setattr(util.os, "replace", fake_replace)
    try:
        util.cross_device_move(src, dst)
    except OSError as exc:
        assert exc.errno == errno.EACCES
    else:
        raise AssertionError("expected OSError")


def test_same_device_compares_two_directories(tmp_path):
    left = tmp_path / "left"
    right = tmp_path / "right"
    left.mkdir()
    right.mkdir()
    assert util.same_device(str(left), str(right)) is True
    assert util.same_device(str(left), str(tmp_path / "missing")) is False


def test_mount_fstype_reports_a_real_filesystem():
    assert util.mount_fstype("/") != ""
