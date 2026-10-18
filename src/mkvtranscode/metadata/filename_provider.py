"""A provider that returns nothing.

Use this with metadata.provider = 'filename' or 'none'. The pipeline then
names the output from the source file name alone.
"""

from __future__ import annotations

from typing import Optional

from .types import Provider, Title


class NullProvider(Provider):
    name = "none"

    def available(self) -> bool:
        return False

    def lookup_id(self, imdb_id: str) -> Optional[Title]:
        return None

    def search(self, title: str, year: Optional[int] = None) -> Optional[Title]:
        return None

    def status(self) -> str:
        return "metadata disabled; names come from the file name"
