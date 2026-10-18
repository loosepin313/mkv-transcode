"""Tests for file-name parsing and output path building."""

import os

import pytest

from mkvtranscode.config import Config
from mkvtranscode.naming import build_output_path, parse_filename, unique_path
from mkvtranscode.util import normalize_title, sanitize_component

# Reuse the shipped defaults so the tests follow the product, not a copy.
PATTERN = Config().naming.pattern
EPISODE_PATTERN = Config().naming.episode_pattern


@pytest.mark.parametrize(
    "name, title, year",
    [
        ("Alien.1979.1080p.BluRay.DTS-HD.MA.5.1.mkv", "Alien", 1979),
        ("Blade Runner 2049 (2017) 2160p HDR.mkv", "Blade Runner 2049", 2017),
        ("The.Witcher.S02E01.1080p.WEB-DL.mkv", "The Witcher", None),
        ("2001 A Space Odyssey 1968.mkv", "2001 A Space Odyssey", 1968),
        ("Alien 3 (1992) [Remux].mkv", "Alien 3", 1992),
        ("Raiders of the Lost Ark 1981 720p x264 AC3 5.1.mkv", "Raiders of the Lost Ark", 1981),
        ("movie.mkv", "movie", None),
        ("Big Buck Bunny 2008.mkv", "Big Buck Bunny", 2008),
    ],
)
def test_parse_filename(name, title, year):
    guess = parse_filename(name)
    assert guess.title == title
    assert guess.year == year


def test_parse_filename_finds_imdb_id_and_episode():
    guess = parse_filename("Alien.1979.1080p.BluRay.tt0078748.mkv")
    assert guess.imdb_id == "tt0078748"
    assert guess.title == "Alien"

    episode = parse_filename("Some.Show.S01E07.1080p.mkv")
    assert episode.episode == "s01e07"


def test_parse_filename_keeps_part_number():
    guess = parse_filename("Kill Bill Part 1 2003.mkv")
    assert guess.part == 1


def test_build_output_path_matches_the_documented_layout(tmp_path):
    path = build_output_path(
        root=str(tmp_path),
        title="Alien",
        year=1979,
        pattern=PATTERN,
    )
    assert path == os.path.join(str(tmp_path), "Alien (1979)", "Alien (1979).mkv")


def test_build_output_path_sanitises_forbidden_characters(tmp_path):
    path = build_output_path(
        root=str(tmp_path),
        title="Alien: Covenant / Director's Cut",
        year=2017,
        pattern=PATTERN,
    )
    relative = os.path.relpath(path, str(tmp_path))
    for forbidden in ':*?"<>|':
        assert forbidden not in relative
    assert relative.startswith("Alien")


def test_build_output_path_without_a_year(tmp_path):
    path = build_output_path(
        root=str(tmp_path),
        title="Untitled",
        year=None,
        pattern=PATTERN,
    )
    assert path.endswith("Untitled/Untitled.mkv")


def test_build_output_path_episode_pattern(tmp_path):
    path = build_output_path(
        root=str(tmp_path),
        title="The Witcher",
        year=2019,
        pattern=EPISODE_PATTERN,
        episode="s02e01",
    )
    assert path.endswith("The Witcher (2019)/The Witcher (2019) - s02e01.mkv")


def test_unknown_pattern_field_raises(tmp_path):
    with pytest.raises(ValueError):
        build_output_path(str(tmp_path), "X", 2000, "{nope} ({year})/{title}.mkv")


def test_unique_path_adds_a_suffix(tmp_path):
    first = tmp_path / "A (2000).mkv"
    first.write_text("x")
    second = unique_path(str(first))
    assert second.endswith("A (2000) (2).mkv")


def test_sanitize_component_removes_control_and_windows_names():
    assert sanitize_component("con") == "_con"
    assert sanitize_component("  spaced  ") == "spaced"
    assert sanitize_component("trailing...") == "trailing"


def test_normalize_title_is_ascii_and_lowercase():
    assert normalize_title("Léon: The Professional") == "leon the professional"
