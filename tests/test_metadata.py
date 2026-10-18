"""Tests for the IMDb dataset provider and the resolver."""

import os

import pytest

from mkvtranscode.metadata import build_provider, resolve_title
from mkvtranscode.metadata.imdb_dataset import ImdbDatasetProvider, remove_dataset
from mkvtranscode.naming import parse_filename

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def test_fetch_builds_an_index_from_the_fixture(imdb_index):
    assert imdb_index.available()
    assert imdb_index.count() == 4
    assert "imdb index has 4 titles" in imdb_index.status()


def test_lookup_by_id(imdb_index):
    found = imdb_index.lookup_id("tt0078748")
    assert found.title == "Alien"
    assert found.year == 1979
    assert found.runtime_minutes == 117
    assert found.primary_genre == "Action"


def test_exact_title_search(imdb_index):
    found = imdb_index.search("Alien 3")
    assert found.imdb_id == "tt0083987"


def test_year_disambiguates_two_titles(imdb_index):
    assert imdb_index.search("Alien 3", 1992).imdb_id == "tt0083987"
    assert imdb_index.search("Alien", 1979).imdb_id == "tt0078748"


def test_fuzzy_search_matches_a_short_name(imdb_index):
    found = imdb_index.search("shawshank redemption")
    assert found and found.imdb_id == "tt0111161"


def test_alternative_title_search(imdb_index):
    found = imdb_index.search("Alien - Das finstere Wesen")
    assert found and found.imdb_id == "tt0078748"


def test_title_type_filter_excludes_series(imdb_index):
    assert imdb_index.lookup_id("tt2361573") is None


def test_missing_index_raises_a_helpful_error(tmp_path):
    provider = ImdbDatasetProvider(data_dir=str(tmp_path / "empty"))
    assert provider.available() is False
    with pytest.raises(Exception) as exc:
        provider.lookup_id("tt0000001")
    assert "imdb fetch" in str(exc.value)


def test_remove_dataset_deletes_the_files(imdb_index, config):
    assert remove_dataset(config.paths.imdb_data) is True
    assert imdb_index.available() is False


def test_resolve_title_uses_the_id_from_the_file_name(config, imdb_index):
    guess = parse_filename("Alien.1979.1080p.BluRay.tt0078748.mkv")
    title = resolve_title(config, guess, imdb_index)
    assert title.imdb_id == "tt0078748"
    assert title.title == "Alien"
    assert title.year == 1979
    assert title.confidence == 1.0


def test_resolve_title_falls_back_to_the_file_name(config, imdb_index):
    guess = parse_filename("Some Movie Nobody Knows 2020.mkv")
    title = resolve_title(config, guess, imdb_index)
    assert title.source == "filename"
    assert title.title == "Some Movie Nobody Knows"
    assert title.year == 2020


def test_resolve_title_without_a_provider(config):
    guess = parse_filename("Alien.1979.mkv")
    title = resolve_title(config, guess, build_provider(config))
    assert title.title == "Alien"
    assert title.source == "filename"


def test_provider_factory_for_each_name(config):
    for name in ("filename", "none"):
        config.metadata.provider = name
        assert build_provider(config).available() is False
    config.metadata.provider = "imdb"
    assert build_provider(config).available() is False  # no index in this test
    config.metadata.provider = "nope"
    with pytest.raises(ValueError):
        build_provider(config)
