# Metadata and naming

The tool names every output file `Title (Year)`. It gets that name from the
source file name and confirms it with IMDb.

## How a name is resolved

1. Read the file name. Remove the extension, the IMDb id, the episode id, the
   year and the release noise.
2. Look for an IMDb id in the file name. A `tt` id wins over everything else.
3. Search the metadata provider for the title and year.
4. If the lookup fails, use the cleaned file name.

A job never fails because metadata is missing.

```
Alien.1979.1080p.BluRay.DTS-HD.MA.5.1.mkv  ->  Alien (1979)
Alien.1979.1080p.BluRay.tt0078748.mkv      ->  Alien (1979)  from IMDb
The.Witcher.S02E01.1080p.WEB-DL.mkv        ->  The Witcher (2019) - s02e01
```

## Providers

Set `metadata.provider`:

| Value | Needs network | Needs a key | Notes |
|-------|---------------|-------------|-------|
| `imdb` (default) | Only for `imdb fetch` | No | Local SQLite index from the IMDb datasets. |
| `imdbpy` | Yes | No | Uses the IMDbPy package. Install it with pip. |
| `filename` | No | No | No lookup at all. |
| `none` | No | No | Same as `filename`. |

## The IMDb dataset index

IMDb publishes free weekly data files at `https://datasets.imdbws.com`. The
tool downloads two files and builds a local index:

```bash
mkv-transcode imdb fetch
```

- `title.basics.tsv.gz` is about 230 MB.
- `title.akas.tsv.gz` is about 130 MB.
- The SQLite index is about 400 MB.
- Refresh it every few weeks. The data files change slowly.

Commands:

```bash
mkv-transcode imdb status
mkv-transcode imdb lookup tt0078748
mkv-transcode imdb search "Alien 3" --year 1992
mkv-transcode imdb remove
```

`imdb search` shows exactly what the pipeline would pick for a name.

### What IMDb gives you

The free datasets carry the title, the original title, the type, the start
year, the runtime and the genres. They do not carry a full release date. The
NFO sidecar writes the year in `<year>` and `<releasedate>`. If you need an
exact release date, let Jellyfin or Kodi fetch it online.

### Offline build

Download the files on another machine, then build the index locally:

```bash
mkv-transcode imdb fetch \
  --from-file /path/title.basics.tsv.gz \
  --akas-file /path/title.akas.tsv.gz
```

### Alternative titles

The `akas` table holds alternative titles. `metadata.akas_countries` limits
which regions the index keeps. The default is `["XWW", "US", "GB"]`. Set it to
an empty list to index every region. That helps when a rip carries a local
title such as `Alien - Das finstere Wesen`.

## Matching rules

The matcher normalises a title to lower-case ASCII without punctuation. It
then tries, in order:

1. Exact match on `primary_title` or `original_title`, with the year.
2. Exact match without the year, nearest year first.
3. Alternative title match.
4. Fuzzy match. The tool ranks candidates with a similarity ratio and needs
   0.80 or better.

A weak match falls back to the file name. The report file records the
confidence value, so you can see how the tool chose a name.

## Output layout

The default pattern is:

```toml
[naming]
pattern = "{title} ({year})/{title} ({year}).mkv"
```

Placeholders: `{title}`, `{year}`, `{episode}`, `{imdb_id}`, `{part}`,
`{ext}`.

The tool sanitises every path component. It replaces `/ \ : * ? " < > |` and
control characters with a space, and it trims trailing dots. Names stay valid
on ext4, NFS and Samba shares.

When the target name already exists, the tool adds ` (2)`, ` (3)` and so on.
Set `naming.keep_source_on_conflict = false` to skip the file instead.

## NFO sidecar

When an IMDb match reaches 0.80 confidence, the tool writes an NFO file next
to the video. Jellyfin reads it and skips its own online lookup.

```xml
<movie>
  <title>Alien</title>
  <year>1979</year>
  <imdbid>tt0078748</imdbid>
  <runtime>117</runtime>
</movie>
```

Set `output.write_nfo = false` to turn this off.

## Language rules

The default language is English. ffmpeg uses ISO 639-2/T codes, so English is
`eng`.

```toml
[language]
default = "eng"
audio_prefer = ["eng"]
subtitle_prefer = ["eng"]
audio_keep = []        # empty keeps every language
subtitle_keep = []
```

What the tool does with these values:

- It puts the preferred-language audio tracks first in the output.
- It sets the default flag on the first preferred audio track.
- It sets the default flag on the first preferred subtitle track.
- It tags a track that has no language with `language.default`.
- `audio_keep` and `subtitle_keep` drop tracks in other languages.

## Report files

Each job writes two files under `paths.logs`:

- `<source-name>.log` — the plan, the ffmpeg command, progress and the result.
- `<source-name>.json` — sizes, durations, the chosen title and the confidence.
