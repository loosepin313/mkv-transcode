"""Write Jellyfin NFO sidecar files.

Jellyfin reads an NFO file next to a media file. This keeps the IMDb data in
the library even when the online lookup is disabled.
"""

from __future__ import annotations

import os
from xml.sax.saxutils import escape

from .metadata.types import Title

TEMPLATE = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<movie>
  <title>{title}</title>
  <originaltitle>{original}</originaltitle>
  <sorttitle>{title}</sorttitle>
  <year>{year}</year>
  <releasedate>{release}</releasedate>
  <imdbid>{imdb}</imdbid>
  <runtime>{runtime}</runtime>
{genres}{plot}</movie>
"""


def write_nfo(path: str, title: Title, release_year_only: bool = True) -> str:
    """Write an NFO file next to the media file and return its path."""
    genres = ""
    if title.primary_genre:
        for genre in title.primary_genre.split("|"):
            genres += "  <genre>%s</genre>\n" % escape(genre)
    year = str(title.year or "")
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    text = TEMPLATE.format(
        title=escape(title.title),
        original=escape(title.original_title or title.title),
        year=year,
        release=year,
        imdb=escape(title.imdb_id or ""),
        runtime=str(title.runtime_minutes or ""),
        genres=genres,
        plot="",
    )
    tmp = path + ".part"
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.replace(tmp, path)
    return path
