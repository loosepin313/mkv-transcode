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


def test_clear_jobs_removes_every_row(tmp_path):
    """The Clear jobs button empties the history, and nothing else."""
    state = make_state(tmp_path)
    state.start_job(record("/in/A.mkv", "running"))
    state.start_job(record("/in/B.mkv", "running"))
    state.finish_job(record("/in/B.mkv", DONE))
    state.note_seen("/in/C.mkv", 10, 0.0)

    assert state.clear_jobs() == 2
    assert state.counts() == {}
    assert state.recent_jobs() == []
    assert state.is_finished("/in/B.mkv") is False
    # The seen table keeps its rows, so the queue keeps its stability history.
    row = state.conn.execute("SELECT COUNT(*) AS n FROM seen").fetchone()
    assert row["n"] == 1
    state.close()


def test_several_threads_share_one_state_store(tmp_path):
    """The watcher runs jobs on threads, so State must be thread safe."""
    import threading

    state = State(os.path.join(str(tmp_path), ".state.sqlite"))
    errors = []

    def work(worker):
        try:
            for index in range(40):
                path = "/in/%d-%d.mkv" % (worker, index)
                record = JobRecord(input=path, source_size=10)
                state.start_job(record)
                state.note_seen(path, 10, 0.0)
                record.status = DONE
                state.finish_job(record)
                state.counts()
        except Exception as exc:                      # keep the failure for the assert
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(n,)) for n in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert state.counts().get("done") == 160
