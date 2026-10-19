# Install

Target systems: Fedora 38 or newer, and Rocky Linux 9. The tool needs Linux,
Python 3.9 or newer, ffmpeg and ffprobe.

## 1. System packages

Run the script. It installs ffmpeg, Python, `nice` and `ionice` with dnf.

```bash
sudo scripts/install-deps.sh --rpmfusion
```

What the script does:

- Enables RPM Fusion Free. Fedora's own `ffmpeg-free` package lacks the
  patent-encumbered encoders, so the `hevc` and `h264` profiles need RPM
  Fusion. RPM Fusion also supplies the full ffmpeg build on Rocky Linux.
- Installs `ffmpeg`. This pulls `svt-av1-libs`, `x265-libs`, `rav1e-libs` and
  `libopus` as dependencies.
- Installs `python3`, `python3-pip` and `python3-venv`.

On Rocky Linux the script also installs EPEL first.

Package names on the two distributions:

| Package | Fedora | Rocky Linux 9 |
|---------|--------|---------------|
| ffmpeg | rpmfusion-free | rpmfusion-free (needs EPEL) |
| x265-libs | rpmfusion-free | rpmfusion-free |
| svt-av1-libs | fedora | fedora / epel |
| python3 | fedora | baseos |
| mkvtoolnix (optional) | fedora or rpmfusion | rpmfusion |

## 2. Check the encoders

`doctor` lists the encoders and filters that ffmpeg exposes.

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/mkv-transcode doctor
```

A ready machine reports:

```
OK    encoder libsvtav1      present
OK    encoders found         libaom-av1, librav1e, libsvtav1, libx264, libx265
OK    filter bwdif           present
OK    inotify                available
```

If `libsvtav1` is missing, your ffmpeg build lacks SVT-AV1. Install the RPM
Fusion build, or set `video.profile = "hevc"`.

## 3. Install the app

System-wide install into `/opt/mkv-transcode` with a launcher in
`/usr/local/bin`:

```bash
sudo scripts/install.sh
```

Development install:

```bash
make venv
.venv/bin/mkv-transcode --help
```

Per-user install with `pipx`:

```bash
pipx install .
```

## 4. Folders

The defaults put every folder under `~/mkv-transcode`. Create them with:

```bash
mkv-transcode init --make-dirs
```

That command writes `mkv-transcode.toml` and creates five folders:

```
~/mkv-transcode/incoming     waiting for a transcode
~/mkv-transcode/workdir      partial output while ffmpeg runs
~/mkv-transcode/transcoded   the finished library
~/mkv-transcode/processed    the source, kept for you
~/mkv-transcode/imdb         the IMDb index and downloads
```

Keep `workdir` on the same file system as `transcoded`. Point the paths at
another disk when you need to:

```toml
[paths]
incoming   = "/data/incoming"
work       = "/data/workdir"
transcoded = "/data/library"
processed  = "/data/processed"
imdb_data  = "/data/imdb"
```

Run `mkv-transcode doctor` to check that each folder exists and is writable.

## 5. IMDb index

The metadata provider uses the free IMDb datasets. Build the index once:

```bash
mkv-transcode imdb fetch
```

The download is about 360 MB. The SQLite index is about 400 MB. See
[METADATA.md](METADATA.md).

Skip this step if you set `metadata.provider = "filename"`. The tool then
names files from the file name only.

## 6. Firewall and SELinux

The tool opens no network ports. It only reads the IMDb datasets during
`imdb fetch`. SELinux in enforcing mode does not block a service that writes
to `/data` or `/srv`. Relabel a custom media directory if needed:

```bash
sudo semanage fcontext -a -t svirt_home_t '/data/media(/.*)?'
sudo restorecon -Rv /data/media
```

## 7. Uninstall

```bash
sudo make uninstall
```

This removes the launcher, the virtual environment and the systemd units. It
leaves `/etc/mkv-transcode/config.toml` and your media files in place.
