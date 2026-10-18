"""Optional IMDbPy provider.

IMDbPy talks to the IMDb web API. It needs the imdbpy package and network
access. The built-in IMDb dataset provider needs neither, so IMDbPy stays
optional. See docs/METADATA.md.
"""

from __future__ import annotations

from typing import Optional

from .types import Provider, Title


class ImdbPyProvider(Provider):
    """Query IMDb through the IMDbPy package."""

    name = "imdbpy"

    def __init__(self, timeout: int = 30):
        self.timeout = timeout
        self._imdb = None
        self._error = ""

    def available(self) -> bool:
        try:
            import imdb  # type: ignore

            self._imdb = imdb
            return True
        except Exception as exc:  # ImportError or a broken install
            self._error = str(exc)
            return False

    def status(self) -> str:
        if self.available():
            return "imdbpy is importable"
        return "imdbpy not usable: %s" % (self._error or "not installed")

    def lookup_id(self, imdb_id: str) -> Optional[Title]:
        if not self._imdb and not self.available():
            return None
        try:
            movie = self._imdb.get_title(imdb_id.lower())
        except Exception:
            return None
        return self._convert(movie)

    def search(self, title: str, year: Optional[int] = None) -> Optional[Title]:
        if not self._imdb and not self.available():
            return None
        try:
            results = self._imdb.search_title(title)
        except Exception:
            return None
        for result in results[:10]:
            if year and result.get("year") and result["year"] != year:
                continue
            movie = self._imdb.get_title(result["id"])
            converted = self._convert(movie)
            if converted:
                converted.confidence = 0.75 if year else 0.6
                return converted
        return None

    def _convert(self, movie) -> Optional[Title]:
        if not movie:
            return None
        try:
            title = movie.get("title") or ""
            if not title:
                return None
            year = movie.get("year")
            runtime = movie.get("runtime")
            if isinstance(runtime, list):
                runtime = runtime[0] if runtime else None
            genres = movie.get("genres") or []
            return Title(
                title=title,
                year=int(year) if year else None,
                imdb_id=movie.get("id"),
                original_title=movie.get("original_title"),
                title_type=(movie.get("title_type") or "").lower(),
                runtime_minutes=int(runtime) if runtime else None,
                primary_genre=genres[0] if genres else "",
                source="imdbpy",
            )
        except Exception:
            return None
