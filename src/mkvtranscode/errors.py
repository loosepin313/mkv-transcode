"""Error types used across the package."""

from __future__ import annotations


class MkvTranscodeError(Exception):
    """Base class for every error this package raises."""


class ConfigError(MkvTranscodeError):
    """The configuration file is invalid or incomplete."""


class ProbeError(MkvTranscodeError):
    """ffprobe failed or returned unusable data."""


class MetadataError(MkvTranscodeError):
    """Metadata lookup failed."""


class NamingError(MkvTranscodeError):
    """The tool cannot build a safe output name."""


class TranscodeError(MkvTranscodeError):
    """ffmpeg failed or produced an unusable file."""


class SkipFile(MkvTranscodeError):
    """The pipeline skips this input on purpose. This is not a failure."""
