# Status web page

`mkv-transcode status-web` serves one page that shows what the watcher is
doing right now. It reads the same files the watcher writes. It changes
nothing.

## What the page shows

| Part | Content |
|------|---------|
| Watcher card | The systemd state of `mkv-transcode.service`, and the `incoming` folder. |
| Now encoding card | The running job, its percent, its pass, and the time left. A bar shows the percent. |
| Jobs done card | Counts for done, failed, skipped and running jobs. |
| Space saved card | Total source size, total output size, and the percent saved. |
| Free space card | Free space in the library folder, and the `min_free_gb` stop level. |
| Job list | Every job in the state file. Search by title, file name or IMDb id. Filter by status. |
| Job detail | Source size, output size, space saved, encode time, started, elapsed, time left, and the profile. Tabs show the ffmpeg plan, the last 400 log lines, and the JSON report. |
| Incoming queue | Files in `incoming`, with size, age, and whether the size is still changing. |

The page refreshes every five seconds. Change that with `--interval`.

## Run it by hand

```bash
mkv-transcode status-web                  # http://127.0.0.1:8090
mkv-transcode status-web --host 0.0.0.0 --port 8090 --interval 5
```

The page needs no write access and no ffmpeg. It runs next to the watcher, or
on another box that can read the same folders.

## Run it as a service

`systemctl enable --now mkv-transcode-status.service` starts the page. The
unit sets `BindsTo=mkv-transcode.service`, so:

- Starting the page also starts the watcher.
- A stopped watcher stops the page.
- The page never runs on its own.

```bash
sudo systemctl enable --now mkv-transcode-status.service
systemctl status mkv-transcode-status.service
journalctl -u mkv-transcode-status -f
```

Open `http://<host>:8090/`.

Change the address or the port with a drop-in:

```bash
sudo systemctl edit mkv-transcode-status.service
```

```ini
[Service]
ExecStart=
ExecStart=/usr/local/bin/mkv-transcode status-web --host 127.0.0.1 --port 9000
```

## Safety

The page has no login and no tokens. The shipped unit binds to `0.0.0.0`, so
any host on your local network can read the job list and the job logs. Keep
the port off the public internet, or bind to `127.0.0.1` and use a tunnel:

```bash
ssh -L 8090:127.0.0.1:8090 <host>
```

The server sends these headers on every response:

- `Content-Security-Policy: default-src 'none'; style-src 'self'; ...`
- `X-Content-Type-Options: nosniff`
- `Cache-Control: no-store`

The server only opens files inside its own `web` folder. A path with `..`
returns 404. The `/api/job` endpoint takes a bare file name, so it cannot
reach outside the logs folder.

## What it reads

| Source | Use |
|--------|-----|
| `<transcoded>/.state.sqlite` | Job rows, counts, and the `seen` table. Opened read only. |
| `<logs>/<name>.log` | Plan lines, progress percent, and notes for one job. |
| `<logs>/<name>.json` | The report for one job. |
| `incoming/` | The queue list. |
| `workdir/` | Partial output size. |
| `systemctl is-active mkv-transcode.service` | The watcher card. |

## Troubleshooting

| Symptom | Cause and fix |
|---------|---------------|
| `cannot listen on 0.0.0.0:8090: [Errno 98] Address already in use` | Another process holds the port. Pass a different `--port`. |
| The page says "No state file yet" | No job ran yet. The watcher creates the file on the first job. |
| The Watcher card says `unknown` | `systemctl` is missing, the unit is not installed, or the sandbox blocks the systemd socket. The unit must allow `AF_UNIX` in `RestrictAddressFamilies`. The rest of the page still works. |
| The percent stays empty | The job log has no `progress pass` line yet. ffmpeg reports progress once the encode starts. |
| A blank page after an upgrade | The web files are missing from the install. Re-run `scripts/install.sh`. |
