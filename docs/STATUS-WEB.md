# Status web page

`mkv-transcode status-web` serves one page that shows what the watcher is
doing right now. It reads the same files the watcher writes. It changes
nothing, except for uploaded files when you turn uploads on.

## What the page shows

| Part | Content |
|------|---------|
| Watcher card | The systemd state of `mkv-transcode.service`, and the `incoming` folder. |
| Now encoding card | The running job, its percent, its pass, and the time left. A bar shows the percent. |
| Jobs done card | Counts for done, failed, skipped and running jobs. |
| Space saved card | Total source size, total output size, and the percent saved. |
| Free space card | Free space in the library folder, and the `min_free_gb` stop level. |
| Upload | Send a file into `incoming` from the browser. Off by default. See Uploads and downloads. |
| IMDb search box | Search the local IMDb index while you upload, then use the hit as the file name. |
| Downloads | Every finished output and its `.nfo`, with a download link. |
| Incoming queue | Files in `incoming`, with size, age, and the reason the watcher skips them. The box shows about four rows and scrolls when more files wait. |
| Job list | Every job in the state file. Search by title, file name or IMDb id. Filter by status. |
| Job detail | Source size, output size, space saved, encode time, started, elapsed, time left, and the profile. Tabs show the ffmpeg plan, the last 400 log lines, and the JSON report. |

The page refreshes every five seconds. Change that with `--interval`.

## Run it by hand

```bash
mkv-transcode status-web                  # http://127.0.0.1:8090
mkv-transcode status-web --host 0.0.0.0 --port 8090 --interval 5
```

The page needs no ffmpeg. It runs next to the watcher, or on another box that
can read the same folders.

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

## Uploads and downloads

Downloads are always on. The page links to each finished output and its `.nfo`
under `paths.transcoded`. The download route only opens files inside that
folder, and only with the extensions it expects. The list shows six files at a
time and scrolls for the rest.

Uploads are off by default, because they write to `incoming`. Turn them on with
`--allow-upload`:

```bash
mkv-transcode status-web --allow-upload --max-upload-gb 60
```

For the service, add a drop-in:

```ini
[Service]
ExecStart=
ExecStart=/usr/local/bin/mkv-transcode status-web --host 0.0.0.0 --port 8090 --interval 5 --allow-upload
```

Then run `sudo systemctl daemon-reload && sudo systemctl restart mkv-transcode-status.service`.

How an upload behaves:

- The name must be a bare file name, and the extension must be in
  `watch.extensions`.
- The server writes to a hidden `.part` file first, then renames it. The
  watcher ignores the hidden name, so a half-finished upload never starts a
  job.
- `--max-upload-gb` caps the size. The default is 60 GiB. `0` means no cap.
- An existing name returns 409. The server never overwrites a file.
- The page also accepts this curl form:

```bash
curl -T "Film (2020).mkv" "http://<host>:8090/api/upload?name=Film%20(2020).mkv"
```

## IMDb search in the upload panel

The upload panel has a search box for the local IMDb index. Type a title, or
paste an IMDb id such as `tt0083658`. The box lists up to 12 hits.

An exact title match comes first, then films, then the newest year. Each hit
shows the id, the type and the main genre.

Click **Use name** and the page fills the **Upload as** field with
`Title (Year).mkv`. That matches `naming.pattern`, so the watcher finds the same
IMDb record when it processes the file. Leave the field empty to upload under
the file name you picked.

The search opens the index read only. It needs the index from
`mkv-transcode imdb fetch`. Without it the box reports the missing index.

The search reads the `norm_primary` index as a range. A hit comes back in a few
milliseconds on a 2.5 million title index. It ranks only the first 500 prefix
hits, which keeps the sort cheap.

## Why a queued file never starts

The queue table shows the reason. The watcher skips a file when:

| Reason shown | Rule |
|--------------|------|
| `below min_size_mb (50 MiB floor)` | The file is smaller than `watch.min_size_mb`. The default is 50 MB, so short clips and test files are ignored. |
| `ignored name` | The name starts with one of `watch.ignore_prefixes`: `.`, `_`, or `sample`. |
| `wrong extension` | The extension is not in `watch.extensions`. |
| `partial file` | The name ends with a partial suffix such as `.part` or `.tmp`. |

To let a small test file through, lower the floor in the config:

```toml
[watch]
min_size_mb = 1.0
```

## Safety

The page has no login and no tokens. The shipped unit binds to `0.0.0.0`, so
any host on your local network can read the job list and the job logs. With
`--allow-upload`, any host on your local network can also write files into
`incoming`. Keep the port off the public internet, or bind to `127.0.0.1` and
use a tunnel:

```bash
ssh -L 8090:127.0.0.1:8090 <host>
```

The server sends these headers on every response:

- `Content-Security-Policy: default-src 'none'; style-src 'self'; ...`
- `X-Content-Type-Options: nosniff`
- `Cache-Control: no-store`

The server only opens files inside its own `web` folder. A path with `..`
returns 404. The `/api/job` and `/api/upload` routes take a bare file name, so
they cannot reach outside the logs folder or the `incoming` folder. The
`/api/file` route accepts folder names, but it resolves them inside the library
first.

## What it reads

| Source | Use |
|--------|-----|
| `<transcoded>/.state.sqlite` | Job rows, counts, and the `seen` table. Opened read only. |
| `<logs>/<name>.log` | Plan lines, progress percent, and notes for one job. |
| `<logs>/<name>.json` | The report for one job. |
| `incoming/` | The queue list, and the target folder for uploads. |
| `workdir/` | Partial output size. |
| `<imdb_data>/imdb.sqlite` | The IMDb search box. Opened read only. |
| `systemctl is-active mkv-transcode.service` | The watcher card. |

## Troubleshooting

| Symptom | Cause and fix |
|---------|---------------|
| `cannot listen on 0.0.0.0:8090: [Errno 98] Address already in use` | Another process holds the port. Pass a different `--port`. |
| The page says "No state file yet" | No job ran yet. The watcher creates the file on the first job. |
| The Watcher card says `unknown` | `systemctl` is missing, the unit is not installed, or the sandbox blocks the systemd socket. The unit must allow `AF_UNIX` in `RestrictAddressFamilies`. The rest of the page still works. |
| The percent stays empty | The job log has no `progress pass` line yet. ffmpeg reports progress once the encode starts. |
| The IMDb box reports a missing index | Run `mkv-transcode imdb fetch`, or point `paths.imdb_data` at the folder with `imdb.sqlite`. |
| A blank page after an upgrade | The web files are missing from the install. Re-run `scripts/install.sh`. |
