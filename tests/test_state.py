"""Tests for the job state file."""

import os

from mkvtranscode.state import DONE, FAILED, JobRecord, State


def make_state(tmp_path):
    return State(os.path.join(str(tmp_path), "state.sqlite"))


def record(path, status, size=100):
    return JobRecord(input=path, status=status, started=1.0, source_size=size)


def test_finished_matches_the_exact_path(tmp_path):
    state = make_state(tmp_path)
    state.start_job(record("/data/incoming/A.mkv", "running"))
    state.finish_job(record("/data/incoming/A.mkv", DONE))
    assert state.is_finished("/data/incoming/A.mkv") is True
    assert state.is_finished("/data/incoming/B.mkv") is False
    state.close()


def test_finished_matches_after_a_folder_move(tmp_path):
    state = make_state(tmp_path)
    state.start_job(record("/old/incoming/A.mkv", "running"))
    state.finish_job(record("/old/incoming/A.mkv", DONE))
    # The user moved the folders, so the stored path no longer exists.
    assert state.is_finished("/new/incoming/A.mkv", 100) is True
    assert state.is_finished("/new/incoming/A.mkv", 999) is False
    state.close()


def test_a_failed_job_is_not_finished(tmp_path):
    state = make_state(tmp_path)
    state.start_job(record("/old/incoming/A.mkv", "running"))
    state.finish_job(record("/old/incoming/A.mkv", FAILED))
    assert state.is_finished("/new/incoming/A.mkv", 100) is False
    state.close()


def test_a_name_with_a_wildcard_still_matches(tmp_path):
    state = make_state(tmp_path)
    state.start_job(record("/old/incoming/A%B_c.mkv", "running"))
    state.finish_job(record("/old/incoming/A%B_c.mkv", DONE))
    assert state.is_finished("/new/incoming/A%B_c.mkv", 100) is True
    assert state.is_finished("/new/incoming/AXBYC.mkv", 100) is False
    state.close()
