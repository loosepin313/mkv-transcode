# Command reference

The common flags work in either position. These two lines do the same thing.

```bash
mkv-transcode --profile hevc plan incoming/Alien.mkv
mkv-transcode plan --profile hevc incoming/Alien.mkv
```

## Global flags

| Flag | Effect |
|------|--------|
| `-c, --config PATH` | Read this config file. |
| `--incoming DIR` | Override `paths.incoming`. |
| `--transcoded DIR` | Override `paths.transcoded`. |
| `--processed DIR` | Override `paths.processed`. |
| `--work DIR` | Override `paths.work`. |
| `--profile {av1,hevc,h264,copy}` | Override `video.profile`. |
| `--crf N` | Override the CRF of the active profile. |
| `--preset VALUE` | Override the preset of the active profile. |
| `--audio-codec {opus,aac}` | Override `audio.codec`. |
| `--audio-policy {copy,transcode,smart}` | Override `audio.policy`. |
| `--target-size-gb N` | Override `video.target_size_gb` and use two passes. |
| `--provider {imdb,imdbpy,filename,none}` | Override `metadata.provider`. |
| `--log-level {debug,info,warning,error}` | Override `job.log_level`. |
| `--dry-run` | Plan and print, but change nothing. |
| `--version` | Print the version. |

## Commands

### doctor

Check the machine. The command prints one line per check and exits 1 when a
check fails.

```bash
mkv-transcode doctor
```

```
OK    distribution           Fedora Linux 44 (Workstation Edition)
OK    ffmpeg                 /usr/bin/ffmpeg
OK    encoder libsvtav1      present
OK    filter bwdif           present
WARN  metadata               imdb index missing; run 'mkv-transcode imdb fetch'
WARN  systemd unit           not installed; see docs/SYSTEMD.md
```

### plan

Print the plan for one file. This writes nothing.

```bash
mkv-transcode plan incoming/Alien.1979.1080p.BluRay.mkv
mkv-transcode plan --json incoming/Alien.1979.mkv
```

The output lists the chosen title, every source stream, the decision for each
stream, the filters and the full ffmpeg command. `--json` gives the same data
for a script.

### once

Scan `incoming` once, process every ready file, then exit.

```bash
mkv-transcode once
mkv-transcode once --wait 1800
```

`--wait` bounds how long the command stays alive while a large rip is still
copying. Without `--wait`, the bound is the larger of 60 seconds or six times
`watch.stable_seconds`. The command logs the files it gave up on and exits 1.

### run

Watch `incoming` and start a job when a file is ready. This is the daemon
mode. It exits on SIGTERM or SIGINT after the running job finishes.

```bash
mkv-transcode run
mkv-transcode run --once        # same as the 'once' command
```

### process

Process named files now. The command ignores the readiness rules.

```bash
mkv-transcode process /data/incoming/Alien.mkv
mkv-transcode process --force /data/incoming/Alien.mkv
```

`--force` reprocesses a file that the state file already marks as done.

### list

List the files in `incoming`, whether they are ready, and their job state.

```bash
mkv-transcode list
```

```
ready     done        8.4 MiB  Good.1979.mkv
copying   -           2.1 MiB  StillCopying.mkv
```

### status

Show job counts and the recent job list.

```bash
mkv-transcode status
```

### imdb

Manage the local IMDb index.

```bash
mkv-transcode imdb fetch
mkv-transcode imdb fetch --from-file title.basics.tsv.gz --akas-file title.akas.tsv.gz
mkv-transcode imdb status
mkv-transcode imdb lookup tt0078748
mkv-transcode imdb search "Alien 3" --year 1992
mkv-transcode imdb remove
```

### config and config-file

`config` prints the effective configuration as JSON. `config-file` prints the
defaults as TOML, which is what a new config file should start from.

```bash
mkv-transcode config > effective.json
mkv-transcode config-file > /etc/mkv-transcode/config.toml
```

### init

Write a config file, and optionally create the folders.

```bash
mkv-transcode init --make-dirs
mkv-transcode init --path /etc/mkv-transcode/config.toml
```

The command refuses to overwrite an existing file.

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | Success. |
| 1 | A job failed, or a doctor check failed. |
| 2 | Bad command line or bad config file. |
| 130 | The user pressed Ctrl-C. |

## Scripting examples

Process a folder and fail the shell script when a job fails:

```bash
mkv-transcode once || echo "a job failed, see transcoded/.logs" >&2
```

Show what would happen to every file, then act:

```bash
for f in incoming/*.mkv; do mkv-transcode plan "$f"; done
```

Re-run one file with a different profile:

```bash
mkv-transcode --profile hevc process --force incoming/Alien.mkv
```
