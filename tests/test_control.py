"""Tests for the request file the status page writes for the watcher."""

import json
import os
import time

from mkvtranscode import control


def test_a_request_round_trips(config):
    payload = control.request_process_now(config)
    found = control.read_request(config)
    assert found["action"] == control.PROCESS_NOW
    assert found["expires"] == payload["expires"]
    assert control.is_live(found) is True
    assert control.expires_in(found) > 0


def test_the_request_file_is_owner_only(config):
    control.request_process_now(config)
    assert os.stat(control.request_path(config)).st_mode & 0o777 == 0o600
    # The write goes through a temporary name, so no part file may survive.
    assert not [n for n in os.listdir(config.paths.transcoded) if n.endswith(".part")]


def test_a_second_click_extends_one_request(config):
    first = control.request_process_now(config, ttl=30)
    second = control.request_process_now(config, ttl=60)
    found = control.read_request(config)
    assert found["expires"] == second["expires"] > first["expires"]


def test_an_expired_request_is_not_live(config):
    control.request_process_now(config, ttl=1)
    request = control.read_request(config)
    later = time.time() + 10
    assert control.is_live(request, now=later) is False
    assert control.expires_in(request, now=later) == 0.0


def test_read_request_ignores_a_damaged_or_foreign_file(config):
    path = control.request_path(config)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("{not json")
    assert control.read_request(config) is None
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"action": "cancel_job"}, handle)
    assert control.read_request(config) is None


def test_clear_request_removes_the_file(config):
    control.request_process_now(config)
    control.clear_request(config)
    assert not os.path.exists(control.request_path(config))
    control.clear_request(config)          # a second call stays quiet
