"""Metadata types and provider interface."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class Title:
    """One IMDb title, or the best guess when no provider is available."""

    title: str
    year: Optional[int] = None
    imdb_id: Optional[str] = None
    original_title: Optional[str] = None
    title_type: str = ""
    runtime_minutes: Optional[int] = None
    primary_genre: str = ""
    source: str = "filename"
    confidence: float = 0.0

    def display_title(self, use_original: bool = False) -> str:
        """Return the title to use in the folder and file name."""
        if use_original and self.original_title:
            return self.original_title
        return self.title

    def summary(self) -> str:
        """One line that shows what the pipeline found."""
        parts = [self.title]
        if self.year:
            parts.append("(%s)" % self.year)
        if self.imdb_id:
            parts.append(self.imdb_id)
        parts.append("[%s]" % self.source)
        return " ".join(parts)


class Provider:
    """Base class for metadata providers."""

    name = "base"

    def available(self) -> bool:
        """True when the provider can answer queries now."""
        raise NotImplementedError

    def lookup_id(self, imdb_id: str) -> Optional[Title]:
        """Return the title for an IMDb id such as tt0078748."""
        raise NotImplementedError

    def search(self, title: str, year: Optional[int] = None) -> Optional[Title]:
        """Return the best match for a title and an optional year."""
        raise NotImplementedError

    def status(self) -> str:
        """A short line for 'mkv-transcode doctor'."""
        return self.name
