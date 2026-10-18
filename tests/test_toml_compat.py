"""Tests for the small TOML reader used on Python 3.9 and 3.10."""

import pytest

from mkvtranscode import toml_compat


def parse(text: str) -> dict:
    """Force the built-in parser, whatever interpreter runs the tests."""
    return toml_compat._loads(text)


def test_scalars_and_comments():
    data = parse(
        "# comment\n"
        'name = "value"  # trailing comment\n'
        "count = 12\n"
        "ratio = 1.5\n"
        "on = true\n"
        "off = false\n"
    )
    assert data == {"name": "value", "count": 12, "ratio": 1.5, "on": True, "off": False}


def test_sections_and_nested_sections():
    data = parse('[paths]\nincoming = "/a"\n[video.av1]\ncrf = 26\npreset = 4\n')
    assert data["paths"]["incoming"] == "/a"
    assert data["video"]["av1"] == {"crf": 26, "preset": 4}


def test_arrays():
    data = parse('ext = ["mkv", "mp4"]\nnums = [1, 2, 3]\nempty = []\n')
    assert data["ext"] == ["mkv", "mp4"]
    assert data["nums"] == [1, 2, 3]
    assert data["empty"] == []


def test_hash_inside_a_string_is_not_a_comment():
    data = parse('title = "Alien #1"\n')
    assert data["title"] == "Alien #1"


def test_bad_line_raises():
    with pytest.raises(ValueError):
        parse("this is not toml\n")
    with pytest.raises(ValueError):
        parse('key = "unterminated\n')
    with pytest.raises(ValueError):
        parse("[bad section name!]\n")


def test_load_reads_a_file(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[watch]\nstable_seconds = 3.0\n', encoding="utf-8")
    assert toml_compat.load(str(path)) == {"watch": {"stable_seconds": 3.0}}
