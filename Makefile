# mkv-transcode - development and installation tasks.
# Run 'make help' for the list of targets.

PYTHON ?= python3
VENV   := .venv
BIN    := $(VENV)/bin/mkv-transcode
PREFIX ?= /opt/mkv-transcode

.PHONY: help venv test lint doctor plan once run clean install install-systemd install-user uninstall config

help:
	@echo "targets:"
	@echo "  venv             create .venv and install the package in editable mode"
	@echo "  test             run the unit tests"
	@echo "  doctor           check this machine and the active configuration"
	@echo "  config           print the default configuration as TOML"
	@echo "  run              start the watcher in the foreground"
	@echo "  once             scan incoming once and process every ready file"
	@echo "  install          install into $(PREFIX) with a launcher in /usr/local/bin"
	@echo "  install-systemd  install the app and the system units"
	@echo "  install-user     install the app and the user units"
	@echo "  uninstall        remove the launcher, the venv and the units"
	@echo "  clean            remove build and cache files"

venv:
	$(PYTHON) -m venv $(VENV)
	$(VENV)/bin/pip install --upgrade pip >/dev/null
	$(VENV)/bin/pip install -e .

test: venv
	$(VENV)/bin/pip install -e '.[dev]' >/dev/null
	$(VENV)/bin/python -m pytest -q

doctor: $(BIN)
	$(BIN) doctor

config: $(BIN)
	$(BIN) config-file

run: $(BIN)
	$(BIN) run

once: $(BIN)
	$(BIN) once

install:
	sudo scripts/install.sh

install-systemd:
	sudo scripts/install.sh --systemd

install-user:
	sudo scripts/install.sh --user

uninstall:
	sudo rm -f /usr/local/bin/mkv-transcode
	sudo rm -rf $(PREFIX)
	sudo rm -f /etc/systemd/system/mkv-transcode.service \
	          /etc/systemd/system/mkv-transcode.path \
	          /etc/systemd/system/mkv-transcode-scan.service \
	          /etc/systemd/system/mkv-transcode-scan.timer
	sudo systemctl daemon-reload

clean:
	rm -rf build dist *.egg-info .pytest_cache
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
	find . -name '*.pyc' -delete
