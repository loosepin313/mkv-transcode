"""Metadata providers and the resolver.

The resolver combines the name guess with a provider lookup. It never fails a
job because metadata is missing. A missing lookup falls back to the file name.
"""

from __future__ import annotations

from typing import Optional

from ..config import Config
from ..naming import Guess
from .types import Provider, Title

__all__ = ["Provider", "Title", "build_provider", "resolve_title"]


def build_provider(config: Config) -> Provider:
    """Return the provider named in metadata.provider."""
    name = config.metadata.provider
    if name in ("filename", "none"):
        from .filename_provider import NullProvider

        return NullProvider()
    if name == "imdb":
        from .imdb_dataset import ImdbDatasetProvider

        return ImdbDatasetProvider(
            data_dir=config.paths.imdb_data,
            title_types=config.metadata.title_types,
            include_akas=config.metadata.include_akas,
            akas_countries=config.metadata.akas_countries,
        )
    if name == "imdbpy":
        from .imdbpy_provider import ImdbPyProvider

        return ImdbPyProvider()
    raise ValueError("unknown metadata provider: %s" % name)


def resolve_title(config: Config, guess: Guess, provider: Optional[Provider] = None) -> Title:
    """Return the Title to use for a source file.

    Order of work:
    1. Use an IMDb id from the file name when one is present.
    2. Search the provider by title and year.
    3. Fall back to the file name guess.
    """
    provider = provider or build_provider(config)
    if provider.available():
        if guess.imdb_id and config.metadata.prefer_id_in_filename:
            found = provider.lookup_id(guess.imdb_id)
            if found:
                found.confidence = 1.0
                return _merge(found, guess)
        found = provider.search(guess.title, guess.year)
        if found:
            if guess.imdb_id and not found.imdb_id:
                found.imdb_id = guess.imdb_id
            return _merge(found, guess)
    return Title(
        title=guess.title,
        year=guess.year,
        imdb_id=guess.imdb_id,
        source="filename",
        confidence=0.4,
    )


def _merge(title: Title, guess: Guess) -> Title:
    """Keep the parts of the guess that IMDb does not carry."""
    if guess.imdb_id and not title.imdb_id:
        title.imdb_id = guess.imdb_id
    return title
