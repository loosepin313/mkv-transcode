# Contributing

## Set up a development copy

```bash
git clone <your-fork>
cd mkv-transcode
make venv
.venv/bin/python -m pytest -q
```

## Run the tests

```bash
python3 -m pytest -q
```

Tests that call ffmpeg skip when ffmpeg is absent. Install ffmpeg to run the
whole suite.

Add a test with every change. The suite covers the plan builder, the naming
rules, the scanner, the IMDb index and the CLI.

## Style

- Python 3.9 compatible. Do not use match statements or `X | None` in
  annotations at runtime.
- No new runtime dependencies. The package uses the standard library, ffmpeg
  and ffprobe only.
- Keep comments short and factual.
- Write documentation in plain English. Use short sentences.

## Add a video profile

1. Add a `VideoProfile` field to `VideoConfig` in `config.py`.
2. Add the name to `VALID_PROFILES`.
3. Add a test in `tests/test_command.py`.
4. Add the profile to the table in `docs/CODEC-CHOICE.md`.

## Add a metadata provider

1. Subclass `Provider` in `src/mkvtranscode/metadata/`.
2. Register the name in `metadata.build_provider`.
3. Add the name to `VALID_METADATA_PROVIDER` and to the CLI choices.
4. Add tests in `tests/test_metadata.py`.

## Report a problem

Include these items:

- The output of `mkv-transcode doctor`.
- The job log from `transcoded/.logs/`.
- The output of `ffprobe` for the source file.
- The distribution and the ffmpeg version.

Remove personal paths before you post a log.
