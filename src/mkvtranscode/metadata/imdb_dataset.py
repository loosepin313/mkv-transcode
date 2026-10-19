"""Offline IMDb metadata from the official IMDb datasets.

IMDb publishes free weekly data files at https://datasets.imdbws.com. This
provider downloads title.basics.tsv.gz once and builds a small SQLite index.
The pipeline then answers every lookup without network access. This keeps the
tool free of API keys and free of screen scraping.
"""

from __future__ import annotations

import difflib
import gzip
import os
import sqlite3
import urllib.request
from typing import Callable, Iterable, List, Optional

from ..errors import MetadataError
from ..util import ensure_dir, human_size, normalize_title
from .types import Provider, Title

BASICS_URL = "https://datasets.imdbws.com/title.basics.tsv.gz"
AKAS_URL = "https://datasets.imdbws.com/title.akas.tsv.gz"
DB_NAME = "imdb.sqlite"
BATCH = 5000

# Rank title types so a film wins over a short or a bonus feature when
# several entries share one name.
TYPE_RANK = (
    "CASE title_type "
    "WHEN 'movie' THEN 0 WHEN 'tvSeries' THEN 1 WHEN 'tvMovie' THEN 2 "
    "WHEN 'tvMiniSeries' THEN 3 WHEN 'video' THEN 6 WHEN 'short' THEN 7 "
    "ELSE 4 END"
)
TYPE_BONUS = {
    "movie": 0.06, "tvSeries": 0.05, "tvMovie": 0.04, "tvMiniSeries": 0.03,
    "video": -0.06, "short": -0.08,
}
# A filename year further away than this marks a weak match.
YEAR_TOLERANCE = 3

# Column order used by the IMDb data files. The loader reads the header first
# and falls back to this order when a name is missing.
BASICS_SCHEMA = [
    "tconst", "titleType", "primaryTitle", "originalTitle", "isAdult",
    "startYear", "endYear", "runtimeMinutes", "genres",
]
AKAS_SCHEMA = [
    "titleId", "ordering", "title", "region", "language", "types",
    "attributes", "isOriginalTitle",
]

SCHEMA = """
PRAGMA journal_mode = OFF;
PRAGMA synchronous = OFF;
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE titles (
    imdb_id TEXT PRIMARY KEY,
    primary_title TEXT NOT NULL,
    original_title TEXT,
    norm_primary TEXT NOT NULL,
    norm_original TEXT,
    title_type TEXT,
    year INTEGER,
    runtime INTEGER,
    primary_genre TEXT
);
CREATE INDEX idx_norm_primary ON titles (norm_primary);
CREATE INDEX idx_norm_original ON titles (norm_original);
CREATE INDEX idx_year ON titles (year);
CREATE TABLE akas (
    imdb_id TEXT NOT NULL,
    norm_title TEXT NOT NULL,
    title TEXT,
    PRIMARY KEY (imdb_id, norm_title)
);
CREATE INDEX idx_akas_norm ON akas (norm_title);
"""


class ImdbDatasetProvider(Provider):
    """Query a local SQLite index built from the IMDb datasets."""

    name = "imdb"

    def __init__(self, data_dir: str, title_types: Optional[Iterable[str]] = None,
                 include_akas: bool = True, akas_countries: Optional[Iterable[str]] = None):
        self.data_dir = data_dir
        self.db_path = os.path.join(data_dir, DB_NAME)
        self.title_types = set(title_types or [])
        self.include_akas = include_akas
        self.akas_countries = set(akas_countries or [])

    # ---- state ---------------------------------------------------------

    def available(self) -> bool:
        return os.path.isfile(self.db_path)

    def status(self) -> str:
        if not self.available():
            return "imdb index missing; run 'mkv-transcode imdb fetch'"
        count = self.count()
        size = human_size(os.path.getsize(self.db_path))
        return "imdb index has %d titles (%s)" % (count, size)

    def connect(self, read_only: bool = True) -> sqlite3.Connection:
        if not self.available():
            raise MetadataError(
                "IMDb index not found at %s. Run 'mkv-transcode imdb fetch' first."
                % self.db_path
            )
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def count(self) -> int:
        if not self.available():
            return 0
        conn = sqlite3.connect(self.db_path)
        try:
            return int(conn.execute("SELECT COUNT(*) FROM titles").fetchone()[0])
        finally:
            conn.close()

    # ---- lookups -------------------------------------------------------

    def lookup_id(self, imdb_id: str) -> Optional[Title]:
        conn = self.connect()
        try:
            row = conn.execute(
                "SELECT * FROM titles WHERE imdb_id = ?", (imdb_id.lower(),)
            ).fetchone()
            return _title_from_row(row) if row else None
        finally:
            conn.close()

    def search(self, title: str, year: Optional[int] = None) -> Optional[Title]:
        norm = normalize_title(title)
        if not norm:
            return None
        conn = self.connect()
        try:
            found = self._exact(conn, norm, year)
            if found:
                return found
            found = self._through_akas(conn, norm, year)
            if found:
                return found
            return self._fuzzy(conn, norm, year)
        finally:
            conn.close()

    def _exact(self, conn, norm: str, year: Optional[int]) -> Optional[Title]:
        # 1. Title and year both match.
        if year:
            row = conn.execute(
                "SELECT * FROM titles WHERE (norm_primary = ? OR norm_original = ?) "
                "AND year = ? ORDER BY %s, year DESC LIMIT 1" % TYPE_RANK,
                (norm, norm, year),
            ).fetchone()
            if row:
                best = _title_from_row(row)
                best.confidence = 1.0
                return best
            # 2. The year in the file name is part of the title, for example
            # "Blade Runner (2049)", where IMDb lists "Blade Runner 2049".
            combined = "%s %d" % (norm, year)
            row = conn.execute(
                "SELECT * FROM titles WHERE norm_primary = ? OR norm_original = ? "
                "ORDER BY %s, year DESC LIMIT 1" % TYPE_RANK,
                (combined, combined),
            ).fetchone()
            if row:
                best = _title_from_row(row)
                best.confidence = 0.95
                return best
        # 3. Only the title matches. Prefer films, then the closest year.
        rows = conn.execute(
            "SELECT * FROM titles WHERE norm_primary = ? OR norm_original = ? "
            "ORDER BY %s, ABS(COALESCE(year, 0) - ?) LIMIT 5" % TYPE_RANK,
            (norm, norm, year or 0),
        ).fetchall()
        if not rows:
            return None
        best = _title_from_row(rows[0])
        if year and best.year and abs(best.year - year) > YEAR_TOLERANCE:
            best.confidence = 0.7
        elif year:
            best.confidence = 0.85
        else:
            best.confidence = 0.9
        return best

    def _through_akas(self, conn, norm: str, year: Optional[int]) -> Optional[Title]:
        sql = (
            "SELECT t.* FROM akas a JOIN titles t ON t.imdb_id = a.imdb_id "
            "WHERE a.norm_title = ?"
        )
        params: List[object] = [norm]
        if year:
            sql += " AND t.year = ?"
            params.append(year)
        sql += " ORDER BY %s, t.year IS NULL, t.year DESC LIMIT 5" % TYPE_RANK.replace(
            "title_type", "t.title_type")
        try:
            rows = conn.execute(sql, params).fetchall()
        except sqlite3.Error:
            return None
        if not rows:
            return None
        best = _title_from_row(rows[0])
        best.confidence = 0.85
        return best

    def _fuzzy(self, conn, norm: str, year: Optional[int]) -> Optional[Title]:
        """Match candidates that contain the query, then rank by similarity."""
        like = "%" + norm.replace("%", r"\%").replace("_", r"\_") + "%"
        rows = conn.execute(
            "SELECT * FROM titles WHERE norm_primary LIKE ? ESCAPE '\\' "
            "OR norm_original LIKE ? ESCAPE '\\' LIMIT 200",
            (like, like),
        ).fetchall()
        best: Optional[Title] = None
        best_score = 0.0
        for row in rows:
            candidate = _title_from_row(row)
            score = max(
                difflib.SequenceMatcher(None, norm, normalize_title(candidate.title)).ratio(),
                difflib.SequenceMatcher(
                    None, norm, normalize_title(candidate.original_title or "")
                ).ratio(),
            )
            if year and candidate.year:
                gap = abs(candidate.year - year)
                if gap > 3:
                    continue
                score -= gap * 0.02
            score += TYPE_BONUS.get(candidate.title_type, 0.0)
            if score > best_score:
                best_score, best = score, candidate
        if best is None or best_score < 0.80:
            return None
        best.confidence = round(best_score, 3)
        return best

    # ---- index build ---------------------------------------------------

    def fetch(
        self,
        progress: Optional[Callable[[str], None]] = None,
        basics_file: Optional[str] = None,
        akas_file: Optional[str] = None,
        timeout: int = 120,
    ) -> int:
        """Download the datasets and build the index. Return the title count."""
        say = progress or (lambda message: None)
        ensure_dir(self.data_dir)
        tmp_db = self.db_path + ".building"
        if os.path.exists(tmp_db):
            os.unlink(tmp_db)
        conn = sqlite3.connect(tmp_db)
        conn.executescript(SCHEMA)
        basics_path = basics_file or _download(BASICS_URL, self.data_dir, "title.basics.tsv.gz", say, timeout)
        count = self._index_basics(conn, basics_path, say)
        akas_rows = 0
        if self.include_akas:
            try:
                akas_path = akas_file or _download(AKAS_URL, self.data_dir, "title.akas.tsv.gz", say, timeout)
                akas_rows = self._index_akas(conn, akas_path, say)
            except (OSError, MetadataError) as exc:
                say("akas index skipped: %s" % exc)
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('built_titles', ?)", (str(count),)
        )
        conn.commit()
        conn.close()
        os.replace(tmp_db, self.db_path)
        say("index built: %d titles, %d alternative titles" % (count, akas_rows))
        return count

    def _index_basics(self, conn, path: str, say) -> int:
        say("reading %s" % path)
        count = 0
        buffer: List[tuple] = []
        with gzip.open(path, "rt", encoding="utf-8", errors="replace") as handle:
            header = handle.readline().rstrip("\n").split("\t")
            columns = _column_map(header, BASICS_SCHEMA)
            for line in handle:
                fields = line.rstrip("\n").split("\t")
                row = _basics_row(fields, columns, self.title_types)
                if row is None:
                    continue
                buffer.append(row)
                if len(buffer) >= BATCH:
                    conn.executemany(
                        "INSERT OR REPLACE INTO titles VALUES (?,?,?,?,?,?,?,?,?)", buffer
                    )
                    count += len(buffer)
                    buffer = []
                    if count % 100000 == 0:
                        say("indexed %d titles" % count)
        if buffer:
            conn.executemany("INSERT OR REPLACE INTO titles VALUES (?,?,?,?,?,?,?,?,?)", buffer)
            count += len(buffer)
        conn.commit()
        return count

    def _index_akas(self, conn, path: str, say) -> int:
        say("reading %s" % path)
        count = 0
        buffer: List[tuple] = []
        with gzip.open(path, "rt", encoding="utf-8", errors="replace") as handle:
            header = handle.readline().rstrip("\n").split("\t")
            columns = _column_map(header, AKAS_SCHEMA)
            for line in handle:
                fields = line.rstrip("\n").split("\t")
                row = _akas_row(fields, columns, self.akas_countries)
                if row is None:
                    continue
                buffer.append(row)
                if len(buffer) >= BATCH:
                    conn.executemany("INSERT OR IGNORE INTO akas VALUES (?,?,?)", buffer)
                    count += len(buffer)
                    buffer = []
        if buffer:
            conn.executemany("INSERT OR IGNORE INTO akas VALUES (?,?,?)", buffer)
            count += len(buffer)
        conn.commit()
        return count


# ---- helpers -----------------------------------------------------------


def _column_map(header: List[str], fallback: List[str]) -> dict:
    """Map column names to positions, with the published order as a fallback."""
    mapping = {}
    for position, name in enumerate(header):
        mapping[name.strip().lower()] = position
    for position, name in enumerate(fallback):
        mapping.setdefault(name, position)
    return mapping


def _value(fields: List[str], columns: dict, key: str) -> str:
    index = columns.get(key)
    if index is None or index >= len(fields):
        return ""
    text = fields[index]
    return "" if text == "\\N" else text


def _basics_row(fields: List[str], columns: dict, allowed_types: set) -> Optional[tuple]:
    imdb_id = _value(fields, columns, "tconst")
    if not imdb_id.startswith("tt"):
        return None
    title_type = _value(fields, columns, "titleType")
    if allowed_types and title_type and title_type not in allowed_types:
        return None
    if allowed_types and not title_type:
        return None
    primary = _value(fields, columns, "primaryTitle")
    original = _value(fields, columns, "originalTitle")
    year_text = _value(fields, columns, "startYear")
    runtime_text = _value(fields, columns, "runtimeMinutes")
    genres = _value(fields, columns, "genres")
    year = int(year_text) if year_text.isdigit() else None
    runtime = int(runtime_text) if runtime_text.isdigit() else None
    if not primary:
        return None
    genre = genres.split("|")[0] if genres else None
    return (
        imdb_id,
        primary,
        original or None,
        normalize_title(primary),
        normalize_title(original) if original else None,
        title_type or None,
        year,
        runtime,
        genre,
    )


def _akas_row(fields: List[str], columns: dict, regions: set) -> Optional[tuple]:
    imdb_id = _value(fields, columns, "titleId")
    title = _value(fields, columns, "title")
    region = _value(fields, columns, "region")
    types = _value(fields, columns, "types")
    is_original = _value(fields, columns, "isOriginalTitle")
    if not imdb_id.startswith("tt") or not title:
        return None
    if is_original == "1":
        return None
    if regions and region and region not in regions:
        return None
    if types:
        names = {part.strip() for part in types.split(",")}
        if names and not names & {"alternative", "imdbDisplay"}:
            return None
    norm = normalize_title(title)
    if not norm:
        return None
    return (imdb_id, norm, title)


def _title_from_row(row) -> Title:
    return Title(
        title=row["primary_title"],
        year=row["year"],
        imdb_id=row["imdb_id"],
        original_title=row["original_title"],
        title_type=row["title_type"] or "",
        runtime_minutes=row["runtime"],
        primary_genre=row["primary_genre"] or "",
        source="imdb-dataset",
    )


def _download(url: str, directory: str, name: str, say, timeout: int) -> str:
    """Download a file to the data directory and return its path."""
    target = os.path.join(directory, name)
    tmp = target + ".part"
    say("downloading %s" % url)
    request = urllib.request.Request(
        url, headers={"User-Agent": "mkv-transcode/1.0 (metadata index)"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            total = int(response.headers.get("Content-Length") or 0)
            done = 0
            with open(tmp, "wb") as out:
                while True:
                    chunk = response.read(1024 * 256)
                    if not chunk:
                        break
                    out.write(chunk)
                    done += len(chunk)
                    if total and done % (1024 * 1024 * 8) < len(chunk):
                        say("  %s of %s" % (human_size(done), human_size(total)))
    except OSError as exc:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise MetadataError("download failed for %s: %s" % (url, exc))
    os.replace(tmp, target)
    say("saved %s (%s)" % (target, human_size(os.path.getsize(target))))
    return target


def remove_dataset(directory: str) -> bool:
    """Delete the local index and the downloaded data files."""
    removed = False
    for name in (DB_NAME, "title.basics.tsv.gz", "title.akas.tsv.gz"):
        path = os.path.join(directory, name)
        if os.path.exists(path):
            os.unlink(path)
            removed = True
    return removed
