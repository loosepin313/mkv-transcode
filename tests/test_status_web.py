"""Tests for the status page: the collector and the HTTP server."""

import json
import os
import threading
import time
import urllib.error
import urllib.parse
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

    library = os.path.join(config.paths.transcoded, "B (2019)")
    os.makedirs(library, exist_ok=True)
    output = os.path.join(library, "B (2019).mkv")
    with open(output, "wb") as handle:
        handle.write(b"mkv" * 200)
    with open(os.path.join(library, "B (2019).nfo"), "w", encoding="utf-8") as handle:
        handle.write("<movie></movie>\n")
    done = JobRecord(input="/data/incoming/B.mkv", output=output,
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


def fetch_any(port, path):
    """Return (status, body) for any HTTP result."""
    try:
        return fetch(port, path)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


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


def post(port, path, body, content_type="application/octet-stream"):
    request = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path), data=body,
                                     method="POST")
    request.add_header("Content-Type", content_type)
    try:
        with urllib.request.urlopen(request, timeout=5) as done:
            return done.status, done.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def put(port, path, body):
    """curl -T style upload."""
    request = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path), data=body,
                                     method="PUT")
    try:
        with urllib.request.urlopen(request, timeout=5) as done:
            return done.status, done.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def with_server(config, allow_upload=False, max_upload_bytes=0):
    httpd = make_server(config, "127.0.0.1", 0, allow_upload=allow_upload,
                        max_upload_bytes=max_upload_bytes)
    worker = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.1})
    worker.daemon = True
    worker.start()
    return httpd, worker


def test_snapshot_lists_downloads_and_upload_state(tmp_path):
    config = make_config(tmp_path)
    seed(config)
    data = snapshot(config, allow_upload=True, max_upload_bytes=1024)
    kinds = sorted(item["kind"] for item in data["downloads"])
    assert kinds == ["nfo", "output"]
    assert data["downloads"][0]["label"] == "B (2019)"
    assert data["downloads"][0]["path"] == os.path.join("B (2019)", "B (2019).mkv")
    assert data["upload"]["enabled"] is True
    assert data["upload"]["max_bytes"] == 1024
    assert data["upload"]["extensions"] == ["mkv"]


def test_upload_is_off_by_default(tmp_path):
    config = make_config(tmp_path)
    seed(config)
    httpd, worker = with_server(config)
    port = httpd.server_address[1]
    try:
        code, body = post(port, "/api/upload?name=New.mkv", b"data")
        assert code == 403
        assert b"--allow-upload" in body
    finally:
        httpd.shutdown()
        httpd.server_close()
        worker.join(timeout=5)


def test_upload_lands_in_incoming(tmp_path):
    config = make_config(tmp_path)
    seed(config)
    httpd, worker = with_server(config, allow_upload=True, max_upload_bytes=10_000)
    port = httpd.server_address[1]
    try:
        code, body = post(port, "/api/upload?name=New%20Film.mkv", b"x" * 4096)
        assert code == 200
        assert json.loads(body.decode("utf-8"))["size"] == 4096
        target = os.path.join(config.paths.incoming, "New Film.mkv")
        assert os.path.isfile(target) and os.path.getsize(target) == 4096
        # curl -T sends PUT, so that route must work too.
        assert put(port, "/api/upload?name=Via%20PUT.mkv", b"y" * 2048)[0] == 200
        assert os.path.getsize(os.path.join(config.paths.incoming, "Via PUT.mkv")) == 2048
        # The hidden part file must be gone, and the mode must be private.
        assert not [n for n in os.listdir(config.paths.incoming) if n.startswith(".")]
        assert os.stat(target).st_mode & 0o777 == 0o600
    finally:
        httpd.shutdown()
        httpd.server_close()
        worker.join(timeout=5)


def test_upload_rejects_bad_names_sizes_and_duplicates(tmp_path):
    config = make_config(tmp_path)
    seed(config)
    httpd, worker = with_server(config, allow_upload=True, max_upload_bytes=100)
    port = httpd.server_address[1]
    try:
        assert post(port, "/api/upload?name=../escape.mkv", b"x")[0] == 400
        assert post(port, "/api/upload?name=notes.txt", b"x")[0] == 400
        assert post(port, "/api/upload?name=Big.mkv", b"x" * 500)[0] == 413
        post(port, "/api/upload?name=Twice.mkv", b"x" * 10)
        assert post(port, "/api/upload?name=Twice.mkv", b"x" * 10)[0] == 409
    finally:
        httpd.shutdown()
        httpd.server_close()
        worker.join(timeout=5)


def test_download_route_serves_library_files(tmp_path):
    config = make_config(tmp_path)
    seed(config)
    httpd, worker = with_server(config)
    port = httpd.server_address[1]
    try:
        rel = urllib.parse.quote(os.path.join("B (2019)", "B (2019).mkv"))
        code, body = fetch(port, "/api/file?kind=output&path=" + rel)
        assert code == 200 and body == b"mkv" * 200
        assert fetch_any(port, "/api/file?kind=nope&path=x")[0] == 400
        escape = urllib.parse.quote("../" + os.path.basename(str(tmp_path)) + "/x.mkv")
        assert fetch_any(port, "/api/file?kind=output&path=" + escape)[0] == 404
        # The nfo sits in the same folder, so the path must include it.
        nfo = urllib.parse.quote(os.path.join("B (2019)", "B (2019).nfo"))
        assert fetch(port, "/api/file?kind=nfo&path=" + nfo)[0] == 200
        # A video name is not allowed under the nfo kind.
        assert fetch_any(port, "/api/file?kind=nfo&path=" + rel)[0] == 400
        assert fetch(port, "/api/file?kind=log&path=A.mkv.log")[0] == 200
    finally:
        httpd.shutdown()
        httpd.server_close()
        worker.join(timeout=5)


def test_queue_says_why_a_file_is_ignored(tmp_path):
    """The page must give the same reason the scanner uses."""
    config = make_config(tmp_path)
    seed(config)
    with open(os.path.join(config.paths.incoming, "_notes.mkv"), "wb") as handle:
        handle.write(b"x" * 4096)
    with open(os.path.join(config.paths.incoming, "clip.avi"), "wb") as handle:
        handle.write(b"x" * 4096)
    reasons = {item["name"]: item["reason"] for item in snapshot(config)["queue"]}
    assert reasons["D.mkv"] == "below min_size_mb"
    assert reasons["_notes.mkv"] == "ignored name"
    assert reasons["clip.avi"] == "wrong extension"
    assert snapshot(config)["queue"][0]["min_size_bytes"] == 50 * 1024 * 1024


def test_page_orders_the_sections_for_an_operator(tmp_path):
    """Cards, then transfer, then the queue, then the job list."""
    config = make_config(tmp_path)
    httpd, worker = with_server(config)
    port = httpd.server_address[1]
    try:
        page = fetch(port, "/")[1].decode("utf-8")
    finally:
        httpd.shutdown()
        httpd.server_close()
        worker.join(timeout=5)
    order = [page.index(marker) for marker in (
        '<div class="cards">',
        '<h2>Upload to incoming</h2>',
        '<h2>Incoming queue</h2>',
        '<h2>Jobs</h2>',
    )]
    assert order == sorted(order)
    assert 'class="table-wrap"' in page
