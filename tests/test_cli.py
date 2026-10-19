"""Tests for the command line interface."""

import json
import os

from mkvtranscode import toml_compat
from mkvtranscode.cli import build_parser, main, default_config_toml

from support import needs_ffmpeg


def run(argv):
    """Run main() and return the exit code."""
    return main(argv)


def test_default_config_text_parses_and_matches_the_defaults(tmp_path):
    text = default_config_toml()
    assert "[video.av1]" in text
    assert 'profile = "av1"' in text
    assert 'default = "eng"' in text
    # The generated text must load back into the same values.
    path = tmp_path / "defaults.toml"
    path.write_text(text, encoding="utf-8")
    parsed = toml_compat.load(str(path))
    assert parsed["language"]["default"] == "eng"
    assert parsed["video"]["av1"]["encoder"] == "libsvtav1"
    assert parsed["naming"]["pattern"].endswith(".mkv")


def test_help_lists_every_command(capsys):
    parser = build_parser()
    text = parser.format_help()
    for command in ("run", "once", "process", "plan", "list", "status", "imdb", "doctor"):
        assert command in text


def test_no_command_prints_help_and_returns_two(capsys):
    assert main([]) == 2


def test_common_flags_work_after_the_subcommand(capsys):
    # '--profile' is a global flag, but it must also work after 'plan'.
    assert main(["--provider", "filename", "config"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["metadata"]["provider"] == "filename"


def test_dry_run_flag_defaults_to_false():
    parser = build_parser()
    args = parser.parse_args(["status"])
    assert not hasattr(args, "dry_run") or args.dry_run is False
    args = parser.parse_args(["--dry-run", "status"])
    assert args.dry_run is True
    args = parser.parse_args(["status", "--dry-run"])
    assert args.dry_run is True


def test_config_file_command_prints_toml(capsys):
    assert main(["config-file"]) == 0
    assert "[paths]" in capsys.readouterr().out


def test_doctor_reports_the_machine(capsys):
    code = main(["doctor"])
    out = capsys.readouterr().out
    assert "ffmpeg" in out
    assert "result:" in out
    assert code in (0, 1)


def test_imdb_status_without_an_index(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MKVTC_PATHS_IMDB_DATA", str(tmp_path / "no-index"))
    assert main(["imdb", "status"]) == 0
    assert "missing" in capsys.readouterr().out


def test_imdb_lookup_without_an_index_fails(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MKVTC_PATHS_IMDB_DATA", str(tmp_path / "no-index"))
    assert main(["imdb", "lookup", "tt0078748"]) == 1
    assert "imdb fetch" in capsys.readouterr().err


def test_init_writes_a_config_file(tmp_path, capsys):
    target = tmp_path / "mkv-transcode.toml"
    assert main(["init", "--path", str(target)]) == 0
    assert target.is_file()
    # A second run must not overwrite the file.
    assert main(["init", "--path", str(target)]) == 1


def test_init_make_dirs_creates_the_default_tree(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    assert main(["init", "--path", "mkv-transcode.toml", "--make-dirs"]) == 0
    root = tmp_path / "mkv-transcode"
    for name in ("incoming", "transcoded", "processed", "workdir", "imdb"):
        assert (root / name).is_dir(), name


def test_config_command_prints_json(capsys):
    assert main(["config"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["video"]["profile"] == "av1"
    assert payload["language"]["default"] == "eng"


@needs_ffmpeg
def test_plan_command_prints_the_ffmpeg_command(config, sample, capsys):
    assert main(["--provider", "filename", "-c", write_config(config), "plan", sample]) == 0
    out = capsys.readouterr().out
    assert "ffmpeg" in out
    assert "libsvtav1" in out


@needs_ffmpeg
def test_plan_command_json_output(config, sample, capsys):
    assert main(["--provider", "filename", "-c", write_config(config), "plan", "--json", sample]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"][0] == "-hide_banner"
    assert "libsvtav1" in payload["command"]
    assert payload["streams"]


@needs_ffmpeg
def test_profile_override_changes_the_encoder(config, sample, capsys):
    assert main(["--provider", "filename", "--profile", "hevc",
                 "-c", write_config(config), "plan", sample]) == 0
    assert "libx265" in capsys.readouterr().out


def write_config(config):
    """Write the test config to a TOML file and return its path."""
    lines = [
        "[paths]",
        'incoming = "%s"' % config.paths.incoming,
        'transcoded = "%s"' % config.paths.transcoded,
        'imdb_data = "%s"' % config.paths.imdb_data,
        "[watch]",
        "min_size_mb = 0.0",
        "[metadata]",
        'provider = "filename"',
        "[job]",
        "min_free_gb = 0.0",
    ]
    path = os.path.join(os.path.dirname(config.paths.incoming), "test-config.toml")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    return path
