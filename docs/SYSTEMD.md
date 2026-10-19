# Run as a service

Three deployment styles ship in `systemd/`. Pick one.

| Style | Unit files | Good for |
|-------|-----------|----------|
| Long-running watcher | `mkv-transcode.service` | Default. Reacts within one second. |
| Path activation | `mkv-transcode.path` + `mkv-transcode-scan.service` | A box that should not run a daemon. |
| Timer | `mkv-transcode-scan.timer` + `mkv-transcode-scan.service` | A remote or slow mount. |

## Style 1: the long-running watcher

Install the app and the units:

```bash
sudo scripts/install.sh --systemd
```

That creates the service user `mkvtranscode`, copies the units to
`/etc/systemd/system/` and writes `/etc/mkv-transcode/config.toml` if the file
is missing.

Set your paths in the config:

```toml
[paths]
incoming   = "/data/incoming"
work       = "/data/workdir"
transcoded = "/data/library"
processed  = "/data/processed"
```

Create the folders and give the service user access:

```bash
sudo mkdir -p /data/incoming /data/workdir /data/library /data/processed
sudo chown -R mkvtranscode:mkvtranscode /data/incoming /data/workdir \
  /data/library /data/processed
```

The service sets `PrivateTmp=true`. A `work` path under `/tmp` then points at a
private folder that dies with the job. Keep `work` inside your data folders.

Start the service:

```bash
sudo systemctl enable --now mkv-transcode.service
systemctl status mkv-transcode.service
journalctl -u mkv-transcode -f
```

Drop-in to change one setting without editing the unit:

```bash
sudo systemctl edit mkv-transcode.service
```

```ini
[Service]
Environment=MKVTC_VIDEO_PROFILE=hevc
```

### SELinux and home directories

The unit sets `ProtectSystem=full` and `ProtectHome=read-only`. If your media
lives under `/home`, add the shipped drop-in:

```bash
sudo mkdir -p /etc/systemd/system/mkv-transcode.service.d
sudo cp systemd/drop-ins/protect-home.conf /etc/systemd/system/mkv-transcode.service.d/
sudo systemctl daemon-reload && sudo systemctl restart mkv-transcode
```

## Style 2: path activation

Edit the folder in `systemd/mkv-transcode.path` so it matches
`paths.incoming`. Then:

```bash
sudo cp systemd/mkv-transcode.path systemd/mkv-transcode-scan.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now mkv-transcode.path
```

systemd starts the oneshot scan service when a `.mkv` file appears. The scan
service runs `mkv-transcode once --wait 1800`, so it stays alive while a large
rip is still copying.

## Style 3: timer

```bash
sudo cp systemd/mkv-transcode-scan.service systemd/mkv-transcode-scan.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now mkv-transcode-scan.timer
systemctl list-timers 'mkv-transcode*'
```

## User service

A user service can write under `/home` and needs no root rights.

```bash
sudo scripts/install.sh --user
mkdir -p ~/.config/mkv-transcode
mkv-transcode config-file > ~/.config/mkv-transcode/config.toml
systemctl --user enable --now mkv-transcode.service
```

Run it while you are logged out:

```bash
sudo loginctl enable-linger "$USER"
```

## What the service logs

The service writes to stdout, so journald captures it:

```bash
journalctl -u mkv-transcode --since today
journalctl -u mkv-transcode -p warning
```

Each job also writes a full log and a JSON report under `paths.logs`.

## Stop, pause and resume

```bash
sudo systemctl stop mkv-transcode.service      # finishes the running job first
sudo systemctl restart mkv-transcode.service
```

The service handles SIGTERM. It lets the running ffmpeg finish, then exits.
A file that already finished never runs again, because the state file records
it. To force a re-run, delete the row or pass `--force`:

```bash
mkv-transcode process --force /data/incoming/Alien.mkv
```

## Resource limits

The unit sets `Nice=10` and best-effort I/O priority 7. The runner also wraps
ffmpeg with `nice` and `ionice`. Lower the impact further:

```toml
[job]
nice = 19
max_parallel = 1
```

To cap CPU use for SVT-AV1, limit the encoder threads:

```toml
[video]
threads = 4
```
