# Build specification

This file specifies how to build, package, install, verify, and remove
mkv-transcode. It does not cover runtime behaviour. See
[ARCHITECTURE.md](ARCHITECTURE.md) for that.

Source of truth for every fact here: `pyproject.toml`, `Makefile`,
`scripts/install.sh`, `scripts/install-deps.sh`, and `systemd/`.

## Table of contents

- [Scope](#scope)
- [Build prerequisites](#build-prerequisites)
- [Package metadata](#package-metadata)
- [Source layout](#source-layout)
- [Build targets](#build-targets)
- [Install layout](#install-layout)
- [Install styles](#install-styles)
- [Verification gates](#verification-gates)
- [Test build](#test-build)
- [Version and release rules](#version-and-release-rules)
- [Removal](#removal)
- [Known build gaps](#known-build-gaps)
- [Acceptance checklist](#acceptance-checklist)

## Scope

The build produces one Python package, one launcher script, and a set of
systemd unit files. The package has no compiled parts. pip builds it from
source at install time.

| In scope | Out of scope |
|----------|--------------|
| Build toolchain and metadata | Encoder tuning |
| Install layout and file ownership | Watcher behaviour |
| Verification gates | Status page design |
| Removal | Packaging for a distro repository |

## Build prerequisites

| Item | Minimum | Where it comes from | Notes |
|------|---------|---------------------|-------|
| Linux | POSIX, with inotify | Target OS | Fedora 38 or newer, Rocky Linux 9 or 10 |
| Python | 3.9 | `requires-python` in `pyproject.toml` | `toml_compat.py` supplies a TOML reader for Python 3.9 and 3.10 |
| setuptools | 68 | `[build-system]` in `pyproject.toml` | Backend is `setuptools.build_meta` |
| ffmpeg | Any build with the needed encoders | `scripts/install-deps.sh` | RPM Fusion Free on Fedora and Rocky Linux |
| ffprobe | Same build as ffmpeg | Ships inside the ffmpeg package | No distro sells it as its own package |
| `nice`, `ionice` | Present | `coreutils`, `util-linux` | The runner wraps ffmpeg with both |
| pytest | Any | `[project.optional-dependencies] dev` | Test build only |
| git | Any | Optional | Clone and version stamp only |

The package declares no runtime Python dependencies. `dependencies = []` in
`pyproject.toml` is deliberate. The tool uses the standard library, ffmpeg, and
ffprobe only.

Install the system packages first:

```bash
sudo scripts/install-deps.sh --rpmfusion
```

The script needs `dnf`. It supports Fedora and Rocky Linux only. It installs
`ffmpeg coreutils util-linux python3 python3-pip`, plus `python3-venv` on
Fedora. With `--rpmfusion` on Rocky Linux, it installs `epel-release` first.

## Package metadata

| Field | Value |
|-------|-------|
| Name | `mkv-transcode` |
| Version | `0.1.0` |
| License | MIT |
| Console script | `mkv-transcode = "mkvtranscode.cli:main"` |
| Package root | `src/` (src layout, found by `tool.setuptools.packages.find`) |
| Extras | `dev` = pytest, `imdbpy` = imdbpy |
| Package data | `py.typed`, `status/web/*.html`, `*.css`, `*.js`, `status/web/fonts/*` |

A new web asset needs a matching entry under
`[tool.setuptools.package-data]`. Without that entry, pip leaves the file out of
the wheel and the status page loads without it.

## Source layout

```
mkv-transcode/
├── pyproject.toml          build metadata, entry point, pytest config
├── Makefile                build, test, install, and removal targets
├── conftest.py             adds src/ to sys.path so pytest needs no install
├── config.example.toml     default configuration, printed by 'config-file'
├── scripts/
│   ├── install-deps.sh     system packages with dnf
│   └── install.sh          venv, launcher, config, and systemd units
├── systemd/                unit files copied verbatim at install time
│   ├── mkv-transcode.service
│   ├── mkv-transcode-status.service
│   ├── mkv-transcode.path
│   ├── mkv-transcode-scan.service
│   ├── mkv-transcode-scan.timer
│   ├── drop-ins/media-paths.conf
│   └── user/mkv-transcode.service
├── src/mkvtranscode/       the package: cli, config, watcher, scan, pipeline,
│                           transcode/, state, metadata/, status/, doctor
└── tests/                  18 modules, 204 test functions, fixtures/
```

## Build targets

Run `make help` for the list.

| Target | Command it runs | Result |
|--------|-----------------|--------|
| `venv` | `python3 -m venv .venv`, then `pip install -e .` | Editable dev build in `.venv` |
| `test` | `pip install -e '.[dev]'`, then `python -m pytest -q` | Test run |
| `doctor` | `.venv/bin/mkv-transcode doctor` | Machine and config check |
| `config` | `.venv/bin/mkv-transcode config-file` | Default config as TOML |
| `run`, `once`, `status-web` | The matching CLI command | Foreground runs |
| `install` | `sudo scripts/install.sh` | App only |
| `install-systemd` | `sudo scripts/install.sh --systemd` | App plus system units |
| `install-user` | `sudo scripts/install.sh --user` | App plus user units |
| `uninstall` | Removes the launcher, the venv, and the units | Refuses a bad `PREFIX` |
| `clean` | Removes `build`, `dist`, `*.egg-info`, `.pytest_cache`, `__pycache__`, `*.pyc` | Clean tree |

Override the interpreter and the install root with variables:

```bash
make test PYTHON=python3.12
make install-systemd PREFIX=/opt/mkv-transcode
```

## Install layout

`scripts/install.sh` creates these paths.

| Path | Owner | Created when |
|------|-------|--------------|
| `/opt/mkv-transcode` | root | Always. This is the venv. |
| `/usr/local/bin/mkv-transcode` | root, mode 0755 | Always. A two-line `sh` launcher. |
| `/etc/mkv-transcode/config.toml` | root | Only when the file is missing |
| `/etc/systemd/system/mkv-transcode*.service` | root, mode 0644 | With `--systemd` |
| `/etc/systemd/system/mkv-transcode.path` | root, mode 0644 | With `--systemd` |
| `~/.config/systemd/user/` | The invoking user | With `--user` |
| Service user `mkvtranscode` | system | With `--systemd`, when absent |

The service user is a system account with `--home-dir /var/lib/mkv-transcode`
and `--shell /sbin/nologin`. The script never changes media ownership for you.
Create the data folders and set the owner by hand:

```bash
sudo mkdir -p /data/media/incoming /data/media/transcoded \
              /data/media/workdir /data/media/processed /data/media/logs
sudo chown -R mkvtranscode:mkvtranscode /data/media
```

The script runs `systemctl daemon-reload` after it copies the units. It does not
enable or start them. That keeps a first install from encoding your library
before you set the paths.

## Install styles

Pick one. Do not mix them on one machine.

```bash
# 1. App only. No units, no service user.
sudo scripts/install.sh

# 2. System service. Creates mkvtranscode and copies the units.
sudo scripts/install.sh --systemd
sudo systemctl enable --now mkv-transcode.service mkv-transcode-status.service

# 3. User service. No root rights, writes under /home.
sudo scripts/install.sh --user
systemctl --user enable --now mkv-transcode.service
```

See [SYSTEMD.md](SYSTEMD.md) for the path and timer styles.

## Verification gates

Run all three gates before you call a build good.

**Gate 1: unit tests.**

```bash
make test
```

**Gate 2: machine check.**

```bash
mkv-transcode doctor
```

A passing run ends with `result: ready`. `doctor` checks the distribution, the
Python version, ffmpeg, and every encoder. It also checks each filter, every
path, free space, inotify, the IMDb index, the GPUs, and the unit file.

**Gate 3: service check.**

```bash
systemctl is-active mkv-transcode.service mkv-transcode-status.service
journalctl -u mkv-transcode -b --since "2 min ago"
```

Expect a `watching <incoming> (inotify)` line and a `provider status:` line.
Note that the `systemd unit` check in `doctor` only tests that the unit file
exists on disk. It does not test that the unit runs. Gate 3 covers that.

**Exit codes.**

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | A job failed, or the run had failures |
| 2 | Bad command line, or `paths.incoming` does not exist |

## Test build

| Fact | Value |
|------|-------|
| Runner | pytest, config in `[tool.pytest.ini_options]`, `testpaths = ["tests"]` |
| Modules | 18 |
| Test functions | 204 |
| Fixtures | `tests/fixtures/`, including `akas.tsv.gz` |
| Sample media | `tests/make_sample.sh` and the `config` fixture build one small Matroska file with ffmpeg |
| Skip rule | A test that needs ffmpeg or ffprobe skips when the binary is absent |
| Path setup | The root `conftest.py` adds `src/` to `sys.path`, so `python3 -m pytest` works with no install step |

The `config` fixture sets `min_size_mb`, `stable_seconds`, and `min_free_gb` to
zero, and sets `check_open_files = false`. It also sets the metadata provider to
`filename`, so the tests need no IMDb index and no network.

Run one module:

```bash
.venv/bin/python -m pytest tests/test_watcher.py -q
```

## Version and release rules

| Rule | Detail |
|------|--------|
| Single version source | `version` in `pyproject.toml` only |
| Changelog | `CHANGELOG.md` keeps an `Unreleased` section at the top |
| Tags | The repo has no git tags yet |
| Published artifacts | None. pip builds from source. |
| CI | The repo has no CI pipeline. A person runs the gates. |

To build a wheel by hand for an offline box:

```bash
python3 -m pip install build
python3 -m build          # writes dist/*.whl and dist/*.tar.gz
```

Then install the wheel into a fresh venv on the target machine.

## Removal

```bash
make uninstall
```

This removes the launcher, `/opt/mkv-transcode`, and the four system unit
files. It leaves `/etc/mkv-transcode/config.toml`, the service user, the state
database, and your media in place. The target refuses a `PREFIX` of `/` or
`/opt`.

To remove the user units as well:

```bash
systemctl --user disable --now mkv-transcode.service
rm -f ~/.config/systemd/user/mkv-transcode.service
systemctl --user daemon-reload
```

## Known build gaps

Each item below is a real gap in the current build. Fix them in this order.

1. `install.sh --systemd` copies `systemd/*.service` and `systemd/*.path`. It
   never copies `systemd/mkv-transcode-scan.timer`. The timer style needs a
   manual copy, as `docs/SYSTEMD.md` shows.
2. No CI pipeline exists. Nothing runs `make test` on a push.
3. The RPM Fusion repository is a hard need for the `hevc` and `h264` profiles.
   Fedora's own `ffmpeg-free` package lacks those encoders.
4. `make clean` does not remove `.venv`. A stale venv keeps an old copy of the
   package when you switch branches.
5. The app uses the standard library `sqlite3` module. The `sqlite3` command
   line tool is not a build dependency, and most minimal installs lack it.
6. The units set `ProtectHome=read-only`. Media under `/home` needs the
   `systemd/drop-ins/media-paths.conf` drop-in and a `daemon-reload`.

## Acceptance checklist

- [ ] `sudo scripts/install-deps.sh --rpmfusion` exits 0.
- [ ] `mkv-transcode doctor` ends with `result: ready`.
- [ ] `make test` ends with no failures and no errors.
- [ ] `systemctl is-active mkv-transcode.service` prints `active`.
- [ ] The journal shows `watching <incoming> (inotify)`.
- [ ] A test file in `incoming` reaches `start:` and then `done`.
- [ ] The output file and its `.nfo` appear under `paths.transcoded`.
- [ ] `mkv-transcode list` shows the source with state `done`.
- [ ] The status page on port 8090 shows the running job.
