# Configuration

The tool reads settings in this order. A later source wins.

1. Built-in defaults.
2. The first config file found: `$MKVTC_CONFIG`, `./mkv-transcode.toml`,
   `./config.toml`, `~/.config/mkv-transcode/config.toml`,
   `/etc/mkv-transcode/config.toml`.
3. Environment variables named `MKVTC_<SECTION>_<KEY>`.
4. Command line flags.

Print the effective values as JSON with `mkv-transcode config`. Print the
defaults as TOML with `mkv-transcode config-file`.

Every key is optional. An unknown key is an error, so a typo fails at start
up.

## [paths]

| Key | Default | Meaning |
|-----|---------|---------|
| `incoming` | `incoming` | Rips that wait for a transcode. |
| `transcoded` | `transcoded` | Library root for the finished files. |
| `work` | `<parent of transcoded>/workdir` | Partial output. Keep it on the same file system as `transcoded`. |
| `logs` | `<transcoded>/.logs` | Per-job log and JSON report. |
| `processed` | `<parent of transcoded>/processed` | Source files after a good run. |
| `imdb_data` | `imdb` | IMDb index and downloads. |

Paths accept `~`. The state file `.state.sqlite` lives in `transcoded`.

The four working folders sit side by side by default:

A relative path sits next to the config file, so a checkout keeps every folder
inside its own folder:

```
<checkout>/incoming     waiting for a transcode
<checkout>/workdir      partial output while ffmpeg runs
<checkout>/transcoded   the finished library
<checkout>/processed    the source, kept for you
<checkout>/imdb         the IMDb index and downloads
```

`mkv-transcode init --make-dirs` creates all five folders. Write an absolute
path, or a path that starts with `~`, to place a folder anywhere you like.

## [watch]

| Key | Default | Meaning |
|-----|---------|---------|
| `mode` | `auto` | `auto`, `inotify` or `poll`. `auto` picks inotify on Linux. |
| `poll_interval` | `5.0` | Seconds between scans, and the inotify wait timeout. |
| `stable_seconds` | `20.0` | How long the size must stay unchanged before a job starts. |
| `check_open_files` | `true` | Refuse a file that another process holds open. Reads `/proc`. |
| `follow_subdirs` | `false` | Scan sub-folders of `incoming`. |
| `rescan_seconds` | `300.0` | Full scan interval. This catches missed events. |
| `extensions` | `["mkv"]` | File types to accept. |
| `ignore_prefixes` | `[".", "_", "sample"]` | Skip names that start with these strings. |
| `min_size_mb` | `50.0` | Skip files smaller than this. It blocks trailers. |

The scanner skips symbolic links. It only accepts regular files that sit
inside `paths.incoming`. A link cannot point the pipeline at a file somewhere
else on the machine. See [SECURITY.md](../SECURITY.md).

## [video]

| Key | Default | Meaning |
|-----|---------|---------|
| `profile` | `av1` | `av1`, `hevc`, `h264` or `copy`. |
| `deinterlace` | `auto` | `auto` applies bwdif when the source is interlaced. |
| `tonemap` | `auto` | `auto`, `keep` or `tonemap`. See CODEC-CHOICE.md. |
| `max_width` | `0` | Scale down to this width. `0` keeps the source width. |
| `threads` | `0` | Encoder threads. `0` lets the encoder choose. |
| `target_size_gb` | `0.0` | `0` uses CRF. Above `0` runs two passes. |
| `keep_chapters` | `true` | Copy chapters when the source has them. |
| `keep_metadata` | `true` | Copy global metadata tags. |

### [video.av1], [video.hevc], [video.h264]

| Key | av1 | hevc | h264 | Meaning |
|-----|-----|------|------|---------|
| `encoder` | `libsvtav1` | `libx265` | `libx264` | ffmpeg encoder name. |
| `crf` | `26` | `20` | `18` | Constant rate factor. Lower is better. |
| `preset` | `4` | `slow` | `slow` | Speed and quality trade-off. |
| `pixel_format` | `yuv420p10le` | `yuv420p10le` | `yuv420p` | Output pixel format. |
| `quality_args` | `[]` | `[]` | `[]` | Replaces `-crf`. Set this for hardware encoders. |
| `extra_args` | `[]` | `["x265-params=log-level=none"]` | `[]` | Extra flags, written as `key=value`. |

`extra_args` uses `key=value`. The tool turns `x265-params=log-level=none`
into `-x265-params log-level=none`.

## [audio]

| Key | Default | Meaning |
|-----|---------|---------|
| `policy` | `smart` | `copy`, `transcode` or `smart`. |
| `copy_codecs` | `["ac3","eac3","aac","opus","mp3","flac"]` | Codecs that `smart` copies. |
| `codec` | `opus` | Target codec for re-encoding: `opus` or `aac`. |
| `bitrate_per_channel_bps` | `64000` | Bit rate per channel. 5.1 becomes 384000. |
| `min_bitrate_bps` | `128000` | Floor for the computed bit rate. |
| `max_channels` | `6` | Downmix above this. Opus above 5.1 is unreliable in Matroska. |
| `downmix` | `auto` | `auto` or `never`. |
| `loudnorm` | `false` | Apply EBU R128 loudness normalisation to encoded tracks. |
| `keep_commentary` | `true` | `false` drops tracks whose title contains "commentary". |
| `extra_args` | `[]` | Extra flags for encoded audio tracks. |

`policy` values:

- `copy` — copy every audio track. A browser cannot play DTS or TrueHD.
- `transcode` — re-encode every audio track.
- `smart` — copy a track when its codec is in `copy_codecs` and its channel
  count fits `max_channels`. Re-encode the rest. This is the default.

The tool always re-encodes TrueHD, DTS, DTS-HD and PCM, because most players
cannot decode them.

## [subtitles]

| Key | Default | Meaning |
|-----|---------|---------|
| `copy` | `true` | Copy subtitle tracks. `false` drops all subtitles. |
| `default_track` | `preferred` | `preferred`, `source` or `none`. |
| `burn_in` | `false` | Burn the first subtitle into the picture. |
| `forced_only` | `false` | Keep only forced subtitle tracks. |
| `keep_image_tracks` | `true` | Keep PGS and VobSub bitmap tracks. |

`burn_in` needs the subtitles filter in ffmpeg. It renders the first kept
subtitle track. Burn-in needs a font. Install `dejavu-fonts` for good results.

## [language]

ffmpeg uses ISO 639-2/T codes. English is `eng`.

| Key | Default | Meaning |
|-----|---------|---------|
| `default` | `eng` | Tag applied to a track that has no language. |
| `audio_prefer` | `["eng"]` | Order for audio tracks. The first match gets the default flag. |
| `subtitle_prefer` | `["eng"]` | Order for subtitles. The first match gets the default flag. |
| `audio_keep` | `[]` | Keep only these languages. Empty keeps all. |
| `subtitle_keep` | `[]` | Keep only these subtitle languages. |

## [naming]

| Key | Default | Meaning |
|-----|---------|---------|
| `pattern` | see below | Output layout under `transcoded`. |
| `episode_pattern` | see below | Layout used when the file name carries an episode id. |
| `use_original_title` | `false` | Use the IMDb original title instead of the local title. |
| `multi_part` | `true` | Add " (Part 1)" when the file name carries a part number. |
| `keep_source_on_conflict` | `true` | Add " (2)" when the name exists. `false` skips the file. |

The default pattern puts the file in a folder with the same name. Placeholders:
`{title}`, `{year}`, `{episode}`, `{imdb_id}`, `{part}`, `{ext}`.

## [metadata]

| Key | Default | Meaning |
|-----|---------|---------|
| `provider` | `imdb` | `imdb`, `imdbpy`, `filename` or `none`. |
| `prefer_id_in_filename` | `true` | A `tt` id in the file name wins over a title search. |
| `fallback_to_filename` | `true` | Use the cleaned file name when the lookup fails. |
| `title_types` | `["movie","tvMovie","video","tvSpecial","short"]` | IMDb types the index keeps. |
| `include_akas` | `true` | Index alternative titles too. |
| `akas_countries` | `["XWW","US","GB"]` | Regions to index. Empty indexes all regions. |

## [output]

| Key | Default | Meaning |
|-----|---------|---------|
| `container` | `mkv` | Output container. The tool writes Matroska. |
| `overwrite` | `false` | Replace an existing output file. |
| `source_action` | `processed` | `processed` moves the source to `paths.processed`. `keep` leaves it in `incoming`. `delete` removes it. |
| `delete_source` | `false` | Legacy alias for `source_action = "delete"`. |
| `move_source_to` | `""` | Sub folder inside `paths.processed`, or an absolute path. |
| `write_report` | `true` | Write the JSON report. |
| `write_nfo` | `true` | Write an NFO sidecar when the IMDb match is strong. |
| `min_output_ratio` | `0.02` | Fail when the output is smaller than this fraction of the source. |
| `max_duration_drift` | `0.02` | Fail when the output duration differs by this fraction. |

`min_output_ratio` catches a broken encode. A noisy cartoon can also produce a
small file, so raise the value with care.

## [job]

| Key | Default | Meaning |
|-----|---------|---------|
| `max_parallel` | `1` | Jobs at the same time. Keep 1 unless you have many cores or a GPU each. |
| `max_attempts` | `3` | Stop retrying a file after this many failures. |
| `nice` | `10` | Priority for ffmpeg. Higher is friendlier. |
| `timeout_hours` | `12.0` | Kill an ffmpeg pass after this long. |
| `min_free_gb` | `10.0` | Refuse to start below this free space. |
| `log_level` | `info` | `debug`, `info`, `warning` or `error`. |
| `keep_work_dir_on_error` | `true` | Keep the partial file after a failure. |

## [gpu]

GPU pinning for NVENC encodes. See [HARDWARE-ACCEL.md](HARDWARE-ACCEL.md) for the
setup and for what each GPU can encode.

| Key | Default | Meaning |
|-----|---------|---------|
| `devices` | `[]` | GPU indexes, one per job, for example `[0, 1]`. Empty means no pinning. |
| `stats` | `true` | The status page reads `nvidia-smi` and shows one card per GPU. |

The GPU count caps the running jobs. With `devices = [0, 1]` and
`max_parallel = 4`, the watcher still runs two jobs at a time.

## Environment overrides

Prefix any key with `MKVTC_`, then write the section and key in upper case.

```bash
export MKVTC_VIDEO_PROFILE=hevc
export MKVTC_WATCH_MIN_SIZE_MB=200
export MKVTC_OUTPUT_DELETE_SOURCE=true
mkv-transcode once
```

## Worked examples

Small files for a laptop:

```toml
[video]
profile = "av1"

[video.av1]
crf = 30
preset = 6

[audio]
bitrate_per_channel_bps = 48000
```

Best quality, size not a problem:

```toml
[video.av1]
crf = 20
preset = 3
```

A library that keeps the original audio for a home cinema receiver:

```toml
[audio]
policy = "copy"
```

A 4K rip that must stay under 8 GB:

```toml
[video]
target_size_gb = 8.0
```
