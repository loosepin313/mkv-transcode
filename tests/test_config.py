"""Tests for configuration loading, overrides and validation."""

import pytest

from mkvtranscode.config import Config
from mkvtranscode.errors import ConfigError


def test_defaults_are_sane():
    config = Config().resolve()
    assert config.video.profile == "av1"
    assert config.active_profile().encoder == "libsvtav1"
    assert config.language.default == "eng"
    assert config.audio.codec == "opus"
    assert config.output.container == "mkv"
    assert config.naming.pattern.count("{title}") == 2


def test_work_and_logs_derive_from_transcoded():
    config = Config()
    config.paths.transcoded = "/data/library"
    config.resolve()
    assert config.paths.work == "/data/library/.work"
    assert config.paths.logs == "/data/library/.logs"


def test_toml_overrides(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        "[paths]\n"
        'incoming = "/srv/in"\n'
        'transcoded = "/srv/out"\n'
        "[video]\n"
        'profile = "hevc"\n'
        "max_width = 1280\n"
        "[video.hevc]\n"
        "crf = 19\n"
        'preset = "veryslow"\n'
        "[language]\n"
        'default = "eng"\n'
        'audio_prefer = ["eng", "fra"]\n'
        "[audio]\n"
        "max_channels = 8\n",
        encoding="utf-8",
    )
    config = Config.load(str(path))
    assert config.paths.incoming == "/srv/in"
    assert config.video.profile == "hevc"
    assert config.video.hevc.crf == 19
    assert config.video.hevc.preset == "veryslow"
    assert config.language.audio_prefer == ["eng", "fra"]
    assert config.audio.max_channels == 8


def test_unknown_key_reports_the_section(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("[video]\nnot_a_key = 1\n", encoding="utf-8")
    with pytest.raises(ConfigError) as exc:
        Config.load(str(path))
    assert "video.not_a_key" in str(exc.value)


def test_bad_value_type(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("[video]\nmax_width = \"wide\"\n", encoding="utf-8")
    with pytest.raises(ConfigError) as exc:
        Config.load(str(path))
    assert "video.max_width" in str(exc.value)


def test_bad_enum_value(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("[video]\nprofile = \"vp10\"\n", encoding="utf-8")
    with pytest.raises(ConfigError) as exc:
        Config.load(str(path))
    assert "video.profile" in str(exc.value)


def test_environment_override(monkeypatch):
    monkeypatch.setenv("MKVTC_VIDEO_PROFILE", "hevc")
    monkeypatch.setenv("MKVTC_WATCH_MIN_SIZE_MB", "12")
    monkeypatch.setenv("MKVTC_OUTPUT_DELETE_SOURCE", "true")
    config = Config.load(None)
    assert config.video.profile == "hevc"
    assert config.watch.min_size_mb == 12.0
    assert config.output.delete_source is True


def test_naming_pattern_must_have_title_and_year(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[naming]\npattern = "out/{ext}"\n', encoding="utf-8")
    with pytest.raises(ConfigError):
        Config.load(str(path))


def test_target_size_bytes():
    config = Config()
    config.video.target_size_gb = 2.0
    assert config.target_size_bytes() == 2000000000


def test_summary_lists_active_settings():
    lines = Config().resolve().summary()
    joined = "\n".join(lines)
    assert "video:" in joined
    assert "language:" in joined and "eng" in joined
