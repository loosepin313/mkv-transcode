"""Tests for the status page: the collector and the HTTP server."""

import json
import os
import threading
import time
import urllib.error
import urllib.request

from mkvtranscode.config import Config
from mkvtranscode.state import DONE, FAILED, RUNNING, JobRecord, State
from mkvtranscode.status.collector import job_detail, snapshot
from mkvtranscode.status.server import make_server

LOG_TEXT = """---- log started ----
input: /data/incoming/A.mkv
named: A (2020) tt0000001 [imdb-dataset] (confidence 1.00)
video: video    #0 [und] encode:libsvtav1
audio: audio    #1 [eng] encode:libopus 384 kbps, 6 ch
quality: av1 passes=1 crf
audio #1 downmixed from 8 to 6 channels
progress pass 1: 0h 10m (8.0%)
progress pass 1: 1h 00m (50.0%)
"""


def make_config(tmp_path):
    """Build a config whose folders sit inside tmp_path."""
    root = str(tmp_path)
    config = Config()
    config.paths.incoming = os.path.join(root, "incoming")
    config.paths.transcoded = os.path.join(root, "transcoded")
    config.paths.work = os.path.join(root, "workdir")
    config.paths.logs = os.path.join(root, "logs")
    config.paths.processed = os.path.join(root, "processed")
    for folder in (config.paths.incoming, config.paths.transcoded, config.paths.work,
                   config.paths.logs, config.paths.processed):
        os.makedirs(folder, exist_ok=True)
    config.resolve()
    config.validate()
    return config


def seed(config):
    """Write one running job, one done job, one failed job, and one queued file."""
    state = State(os.path.join(config.paths.transcoded, ".state.sqlite"))
    running = JobRecord(input=os.path.join(config.paths.incoming, "A.mkv"),
                        source_size=1000, title="A", year=2020, imdb_id="tt0000001",
                        profile="av1")
    state.start_job(running)
    # Pretend the run started an hour ago, so the page can show an ETA.
    state.conn.execute("UPDATE jobs SET started = ? WHERE input = ?",
                       (time.time() - 3600, running.input))
    state.conn.commit()

    done = JobRecord(input="/data/incoming/B.mkv", output="/lib/B (2019)/B (2019).mkv",
                     source_size=2000, title="B", year=2019,
                     imdb_id="tt0000002", profile="av1")
    state.start_job(done)
    done.status = DONE
    done.output_size = 400
    done.seconds = 100.0
    done.message = "ok"
    state.finish_job(done)

    failed = JobRecord(input="/data/incoming/C.mkv", source_size=10, title="C")
    state.start_job(failed)
    failed.status = FAILED
    failed.seconds = 1.0
    failed.message = "ffmpeg exited with 1"
    state.finish_job(failed)
    state.close()

    with open(os.path.join(config.paths.logs, "A.mkv.log"), "w", encoding="utf-8") as handle:
        handle.write(LOG_TEXT)
    report = {"status": "done", "title": "B", "year": 2019, "saved_percent": 80.0}
    with open(os.path.join(config.paths.logs, "B.mkv.json"), "w", encoding="utf-8") as handle:
        json.dump(report, handle)
    with open(os.path.join(config.paths.incoming, "D.mkv"), "wb") as handle:
        handle.write(b"x" * 4096)


def test_snapshot_reports_counts_and_progress(tmp_path):
    config = make_config(tmp_path)
    seed(config)
    data = snapshot(config)
    assert data["counts"][RUNNING] == 1
    assert data["counts"][DONE] == 1
    assert data["counts"][FAILED] == 1
    assert data["state_exists"] is True

    running = data["running"][0]
    assert running["title"] == "A (2020)"
    assert running["progress"]["percent"] == 50.0
    assert running["progress"]["pass"] == 1
    assert running["eta_seconds"] > 0
    assert any(line.startswith("video:") for line in running["plan"])
    assert any("downmixed" in line for line in running["notes"])

    assert data["totals"]["jobs"] == 1
    assert data["totals"]["saved_percent"] == 80.0
    assert [item["name"] for item in data["queue"]] == ["D.mkv"]
    assert data["queue"][0]["size"] == 4096
    assert data["queue"][0]["seen"] is False


def test_snapshot_works_before_the_first_run(tmp_path):
    config = make_config(tmp_path)
    data = snapshot(config)
    assert data["state_exists"] is False
    assert data["counts"] == {}
    assert data["totals"]["jobs"] == 0
    assert data["running"] == []


def test_job_detail_reads_the_log_and_the_report(tmp_path):
    config = make_config(tmp_path)
    seed(config)
    detail = job_detail(config, "A.mkv")
    assert detail["found"] is True
    assert detail["log_tail"][0] == "---- log started ----"
    report = job_detail(config, "B.mkv")["report"]
    assert report["saved_percent"] == 80.0


def test_plan_lines_survive_a_long_log(tmp_path):
    """A long run writes many progress lines, so the plan sits at the head."""
    config = make_config(tmp_path)
    seed(config)
    path = os.path.join(config.paths.logs, "A.mkv.log")
    with open(path, "a", encoding="utf-8") as handle:
        for index in range(600):
            handle.write("progress pass 1: 0h 01m (%.1f%%)\n" % (index / 6.0))
    detail = job_detail(config, "A.mkv")
    assert detail["plan"][0].startswith("input:")
    assert detail["log_tail"][-1].startswith("progress pass 1:")


def test_job_detail_refuses_a_path_outside_the_logs_folder(tmp_path):
    config = make_config(tmp_path)
    detail = job_detail(config, "../etc/passwd")
    assert detail["found"] is False
    assert detail["reason"] == "bad name"


def fetch(port, path):
    with urllib.request.urlopen("http://127.0.0.1:%d%s" % (port, path), timeout=5) as done:
        return done.status, done.read()


def test_server_serves_the_page_and_the_json(tmp_path):
    config = make_config(tmp_path)
    seed(config)
    httpd = make_server(config, "127.0.0.1", 0)
    port = httpd.server_address[1]
    worker = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.1})
    worker.daemon = True
    worker.start()
    try:
        code, body = fetch(port, "/")
        assert code == 200
        assert b"Transcode Monitor" in body
        code, body = fetch(port, "/app.css")
        assert code == 200 and b"--st-accent" in body
        code, body = fetch(port, "/api/snapshot")
        assert code == 200
        data = json.loads(body.decode("utf-8"))
        assert data["counts"][RUNNING] == 1
        assert "interval" in data
        code, body = fetch(port, "/api/job?name=A.mkv")
        assert json.loads(body.decode("utf-8"))["found"] is True
        try:
            fetch(port, "/../pyproject.toml")
            raise AssertionError("the server served a file outside web/")
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
    finally:
        httpd.shutdown()
        httpd.server_close()
        worker.join(timeout=5)
