"""Tests for the live system numbers on the status page."""

import os

from mkvtranscode.status import system
from mkvtranscode.status.system import (core_count, cpu_model, cpu_percent, memory,
                                        system_stats, temperatures)


def make_proc(tmp_path, cores=4, idle=100, busy=100):
    """Write a small fake /proc tree."""
    proc = tmp_path / "proc"
    proc.mkdir(exist_ok=True)
    lines = []
    for index in range(cores):
        lines.append("processor\t: %d\nmodel name\t: Test CPU @ 3.40GHz\n" % index)
    (proc / "cpuinfo").write_text("".join(lines), encoding="utf-8")
    total_idle = idle
    total_busy = busy
    (proc / "stat").write_text(
        "cpu  %d 0 0 %d 0 0 0 0 0 0\n" % (total_busy, total_idle), encoding="utf-8")
    (proc / "meminfo").write_text(
        "MemTotal:       8192 kB\n"
        "MemFree:         512 kB\n"
        "MemAvailable:   4096 kB\n"
        "Buffers:         128 kB\n"
        "Cached:         1024 kB\n"
        "SwapTotal:      2048 kB\n"
        "SwapFree:       1024 kB\n",
        encoding="utf-8",
    )
    return str(proc)


def make_sys(tmp_path):
    """Write a small fake /sys tree with two temperature sensors."""
    base = tmp_path / "sys" / "class" / "hwmon" / "hwmon0"
    base.mkdir(parents=True, exist_ok=True)
    (base / "name").write_text("k10temp\n", encoding="utf-8")
    (base / "temp1_label").write_text("Tctl\n", encoding="utf-8")
    (base / "temp1_input").write_text("45500\n", encoding="utf-8")
    (base / "temp2_input").write_text("61000\n", encoding="utf-8")
    return str(tmp_path / "sys")


def test_the_cpu_model_and_the_core_count(tmp_path):
    proc = make_proc(tmp_path, cores=6)
    assert cpu_model(proc) == "Test CPU @ 3.40GHz"
    assert core_count(proc) == 6


def test_cpu_use_needs_two_samples(tmp_path):
    proc = make_proc(tmp_path)
    system._PREV.update({"at": 0.0, "busy": 0.0, "total": 0.0})
    assert cpu_percent(proc) is None            # no earlier sample
    # busy went from 100 to 150, total from 200 to 400: a quarter of the time.
    (tmp_path / "proc" / "stat").write_text("cpu  150 0 0 250 0 0 0 0 0 0\n", encoding="utf-8")
    assert cpu_percent(proc) == 25.0


def test_memory_sizes_come_over_in_mebibytes(tmp_path):
    mem = memory(make_proc(tmp_path))
    assert mem["total_mb"] == 8.0
    assert mem["available_mb"] == 4.0
    assert mem["swap_used_mb"] == 1.0
    assert mem["used_mb"] == 4.0


def test_temperatures_are_sorted_with_the_package_first(tmp_path):
    sys_root = make_sys(tmp_path)
    found = temperatures(sys_root)
    assert found[0]["celsius"] == 45.5
    assert found[0]["label"] == "k10temp"
    assert len(found) == 2


def test_a_box_without_sensors_returns_nothing(tmp_path):
    assert temperatures(str(tmp_path / "missing")) == []


def test_the_snapshot_carries_the_system_numbers(config, monkeypatch, tmp_path):
    proc = make_proc(tmp_path)
    sys_root = make_sys(tmp_path)
    monkeypatch.setattr(system, "PROC", proc)
    monkeypatch.setattr(system, "SYS", sys_root)
    system._CACHE["at"] = 0.0
    system._CACHE["data"] = {}
    system._PREV.update({"at": 0.0, "busy": 0.0, "total": 0.0})
    from mkvtranscode.status.collector import snapshot

    data = snapshot(config)
    assert data["system"]["cores"] == 4
    assert data["system"]["cpu_model"] == "Test CPU @ 3.40GHz"
    assert data["system"]["memory"]["total_mb"] == 8.0
    assert data["system"]["hottest_c"] == 45.5
