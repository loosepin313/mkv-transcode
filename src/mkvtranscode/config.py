"""Configuration for the transcode pipeline.

Every setting has a default in this module. A TOML file overrides the
defaults. The loader reports unknown keys and bad values with the exact
section and key that caused the problem.
"""

from __future__ import annotations

import os
from dataclasses import MISSING, dataclass, field, fields, is_dataclass
from typing import Any, Dict, List, Optional

from . import toml_compat
from .errors import ConfigError
from .util import expand

DEFAULT_CONFIG_NAMES = ("mkv-transcode.toml", "config.toml")
ENV_CONFIG = "MKVTC_CONFIG"
ENV_PREFIX = "MKVTC_"

VALID_WATCH_MODES = ("auto", "inotify", "poll")
VALID_DEINTERLACE = ("auto", "always", "never")
VALID_TONEMAP = ("auto", "keep", "tonemap")
VALID_AUDIO_POLICY = ("copy", "transcode", "smart")
VALID_SUBTITLE_DEFAULT = ("source", "preferred", "none")
VALID_METADATA_PROVIDER = ("imdb", "imdbpy", "filename", "none")
VALID_LOG_LEVELS = ("debug", "info", "warning", "error")
VALID_PROFILES = ("av1", "hevc", "h264", "copy")
# What happens to the source file after a successful job.
VALID_SOURCE_ACTION = ("processed", "keep", "delete")


# The default home for every working folder. 'mkv-transcode init --make-dirs'
# builds this tree in the user's home directory.
DEFAULT_ROOT = "~/mkv-transcode"


@dataclass
class PathConfig:
    """The working folders. The defaults put them all under ~/mkv-transcode.

    incoming   files waiting for a transcode
    transcoded finished files, one folder per title
    processed  the source file lands here after a good run
    workdir    partial output while ffmpeg runs
    imdb       the IMDb index and its downloads
    """

    incoming: str = DEFAULT_ROOT + "/incoming"
    transcoded: str = DEFAULT_ROOT + "/transcoded"
    work: str = ""            # empty means "<parent of transcoded>/workdir"
    logs: str = ""            # empty means "<transcoded>/.logs"
    processed: str = ""       # empty means "<parent of transcoded>/processed"
    imdb_data: str = DEFAULT_ROOT + "/imdb"


@dataclass
class WatchConfig:
    mode: str = "auto"                 # auto | inotify | poll
    poll_interval: float = 5.0         # seconds between scans in poll mode
    stable_seconds: float = 20.0       # file size must not change for this long
    check_open_files: bool = True      # refuse a file another process still holds
    follow_subdirs: bool = False       # scan sub-directories of incoming
    rescan_seconds: float = 300.0      # full scan interval, catches missed events
    extensions: List[str] = field(default_factory=lambda: ["mkv"])
    ignore_prefixes: List[str] = field(default_factory=lambda: [".", "_", "sample"])
    min_size_mb: float = 50.0          # ignore trailers and split fragments


@dataclass
class VideoProfile:
    encoder: str = "libsvtav1"
    crf: int = 26
    preset: Any = 4                    # int for SVT-AV1, str for x264/x265
    pixel_format: str = "yuv420p10le"
    # Quality flags. Empty means ['-crf', <crf>]. Hardware encoders such as
    # nvenc have no 'crf' option, so set this list for them.
    quality_args: List[str] = field(default_factory=list)
    extra_args: List[str] = field(default_factory=list)


@dataclass
class VideoConfig:
    profile: str = "av1"               # av1 | hevc | h264 | copy
    deinterlace: str = "auto"          # auto | always | never
    tonemap: str = "auto"              # auto | keep | tonemap
    max_width: int = 0                 # 0 keeps the source width
    threads: int = 0                   # 0 lets the encoder choose
    target_size_gb: float = 0.0        # 0 uses CRF, >0 uses two-pass sizing
    keep_chapters: bool = True
    keep_metadata: bool = True
    av1: VideoProfile = field(default_factory=VideoProfile)
    hevc: VideoProfile = field(
        default_factory=lambda: VideoProfile(
            encoder="libx265", crf=20, preset="slow", pixel_format="yuv420p10le",
            extra_args=["x265-params=log-level=none"],
        )
    )
    h264: VideoProfile = field(
        default_factory=lambda: VideoProfile(
            encoder="libx264", crf=18, preset="slow", pixel_format="yuv420p",
        )
    )


@dataclass
class AudioConfig:
    policy: str = "smart"              # copy | transcode | smart
    copy_codecs: List[str] = field(
        default_factory=lambda: ["ac3", "eac3", "aac", "opus", "mp3", "flac"]
    )
    codec: str = "opus"                # opus | aac
    bitrate_per_channel_bps: int = 64000
    min_bitrate_bps: int = 128000
    max_channels: int = 6              # Opus in Matroska is unreliable above 6
    downmix: str = "auto"              # auto | never
    loudnorm: bool = False             # EBU R128 loudness normalisation
    keep_commentary: bool = True       # false drops tracks titled 'Commentary'
    extra_args: List[str] = field(default_factory=list)


@dataclass
class SubtitleConfig:
    copy: bool = True
    default_track: str = "preferred"   # source | preferred | none
    burn_in: bool = False
    forced_only: bool = False          # keep only forced subtitle tracks
    keep_image_tracks: bool = True     # PGS and VobSub


@dataclass
class LanguageConfig:
    """The default language for audio and subtitle tracks.

    ffmpeg uses ISO 639-2/T codes. 'eng' is the code for English. The list
    forms a preference order, so a source with no English track falls back to
    the next entry.
    """

    default: str = "eng"
    audio_prefer: List[str] = field(default_factory=lambda: ["eng"])
    subtitle_prefer: List[str] = field(default_factory=lambda: ["eng"])
    audio_keep: List[str] = field(default_factory=list)   # empty keeps all
    subtitle_keep: List[str] = field(default_factory=list)


@dataclass
class NamingConfig:
    pattern: str = "{title} ({year})/{title} ({year}).mkv"
    episode_pattern: str = "{title} ({year})/{title} ({year}) - {episode}.mkv"
    use_original_title: bool = False   # IMDb original_title instead of primary_title
    multi_part: bool = True            # add " (Part 1)" for multi-title discs
    keep_source_on_conflict: bool = True


@dataclass
class MetadataConfig:
    provider: str = "imdb"             # imdb | imdbpy | filename | none
    prefer_id_in_filename: bool = True
    fallback_to_filename: bool = True
    title_types: List[str] = field(
        default_factory=lambda: ["movie", "tvMovie", "video", "tvSpecial", "short"]
    )
    include_akas: bool = True          # also match alternative titles
    akas_countries: List[str] = field(default_factory=lambda: ["XWW", "US", "GB"])


@dataclass
class OutputConfig:
    container: str = "mkv"
    overwrite: bool = False
    # processed = move the source to paths.processed, keep = leave it in
    # incoming, delete = remove it. Keep the original unless you have a
    # backup somewhere else.
    source_action: str = "processed"
    delete_source: bool = False          # legacy alias for source_action = "delete"
    move_source_to: str = ""             # sub folder inside paths.processed, or an absolute path
    write_report: bool = True
    write_nfo: bool = True           # Jellyfin sidecar when the IMDb match is strong
    min_output_ratio: float = 0.02     # fail if the output is under 2% of source
    max_duration_drift: float = 0.02   # fail if the output duration differs by this fraction


@dataclass
class JobConfig:
    max_parallel: int = 1
    max_attempts: int = 3              # stop retrying a file after this many failures
    nice: int = 10
    timeout_hours: float = 12.0
    min_free_gb: float = 10.0          # refuse to start below this free space
    log_level: str = "info"
    keep_work_dir_on_error: bool = True


@dataclass
class Config:
    paths: PathConfig = field(default_factory=PathConfig)
    watch: WatchConfig = field(default_factory=WatchConfig)
    video: VideoConfig = field(default_factory=VideoConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    subtitles: SubtitleConfig = field(default_factory=SubtitleConfig)
    language: LanguageConfig = field(default_factory=LanguageConfig)
    naming: NamingConfig = field(default_factory=NamingConfig)
    metadata: MetadataConfig = field(default_factory=MetadataConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    job: JobConfig = field(default_factory=JobConfig)
    source_file: Optional[str] = None

    # ---- loading -------------------------------------------------------

    @classmethod
    def load(cls, path: Optional[str] = None) -> "Config":
        """Build a Config from an optional TOML file plus the environment."""
        config = cls()
        chosen = path or os.environ.get(ENV_CONFIG)
        if chosen:
            if not os.path.isfile(chosen):
                raise ConfigError("config file not found: %s" % chosen)
            config._apply_file(chosen)
        config._apply_env()
        config.resolve()
        config.validate()
        return config

    @classmethod
    def discover(cls) -> "Config":
        """Load the first config file found in the usual places."""
        candidates = [
            os.environ.get(ENV_CONFIG, ""),
            os.path.join(os.getcwd(), "mkv-transcode.toml"),
            os.path.join(os.getcwd(), "config.toml"),
            os.path.expanduser("~/.config/mkv-transcode/config.toml"),
            "/etc/mkv-transcode/config.toml",
        ]
        for candidate in candidates:
            if candidate and os.path.isfile(candidate):
                return cls.load(candidate)
        return cls.load(None)

    def _apply_file(self, path: str) -> None:
        try:
            data = toml_compat.load(path)
        except ValueError as exc:
            raise ConfigError("%s: %s" % (path, exc))
        self.source_file = path
        _bind(self, data, "")

    def _apply_env(self) -> None:
        """Apply MKVTC_SECTION_KEY overrides such as MKVTC_VIDEO_PROFILE."""
        sections = {
            "paths": self.paths,
            "watch": self.watch,
            "video": self.video,
            "audio": self.audio,
            "subtitles": self.subtitles,
            "language": self.language,
            "naming": self.naming,
            "metadata": self.metadata,
            "output": self.output,
            "job": self.job,
        }
        for name, value in os.environ.items():
            if not name.startswith(ENV_PREFIX):
                continue
            parts = name[len(ENV_PREFIX) :].lower().split("_", 1)
            if len(parts) != 2:
                continue
            section, key = parts
            if section not in sections:
                continue
            target = sections[section]
            if not hasattr(target, key):
                continue
            setattr(target, key, _coerce(getattr(target, key), value, "%s.%s" % (section, key)))

    # ---- resolution ----------------------------------------------------

    def resolve(self) -> "Config":
        """Expand paths and fill the derived directories."""
        self.paths.incoming = expand(self.paths.incoming)
        self.paths.transcoded = expand(self.paths.transcoded)
        self.paths.imdb_data = expand(self.paths.imdb_data)
        if not self.paths.work:
            self.paths.work = _sibling(self.paths.transcoded, "workdir", ".work")
        else:
            self.paths.work = expand(self.paths.work)
        if not self.paths.logs:
            self.paths.logs = os.path.join(self.paths.transcoded, ".logs")
        else:
            self.paths.logs = expand(self.paths.logs)
        if not self.paths.processed:
            self.paths.processed = _sibling(self.paths.transcoded, "processed", ".processed")
        else:
            self.paths.processed = expand(self.paths.processed)
        self.watch.extensions = [ext.lstrip(".").lower() for ext in self.watch.extensions]
        return self

    def validate(self) -> "Config":
        """Raise ConfigError when a value is out of range."""
        _one_of(self.watch.mode, VALID_WATCH_MODES, "watch.mode")
        _one_of(self.output.source_action, VALID_SOURCE_ACTION, "output.source_action")
        _one_of(self.video.deinterlace, VALID_DEINTERLACE, "video.deinterlace")
        _one_of(self.video.tonemap, VALID_TONEMAP, "video.tonemap")
        _one_of(self.video.profile, VALID_PROFILES, "video.profile")
        _one_of(self.audio.policy, VALID_AUDIO_POLICY, "audio.policy")
        _one_of(self.subtitles.default_track, VALID_SUBTITLE_DEFAULT, "subtitles.default_track")
        _one_of(self.metadata.provider, VALID_METADATA_PROVIDER, "metadata.provider")
        _one_of(self.job.log_level, VALID_LOG_LEVELS, "job.log_level")
        if self.watch.stable_seconds < 0:
            raise ConfigError("watch.stable_seconds must be 0 or more")
        if self.watch.poll_interval < 0.5:
            raise ConfigError("watch.poll_interval must be 0.5 or more")
        if self.audio.max_channels not in (2, 6, 8):
            raise ConfigError("audio.max_channels must be 2, 6 or 8")
        if self.audio.bitrate_per_channel_bps < 24000:
            raise ConfigError("audio.bitrate_per_channel_bps must be 24000 or more")
        if self.output.min_output_ratio <= 0 or self.output.min_output_ratio > 1:
            raise ConfigError("output.min_output_ratio must be between 0 and 1")
        if not 0 < self.output.max_duration_drift <= 0.5:
            raise ConfigError("output.max_duration_drift must be between 0 and 0.5")
        if self.job.max_attempts < 1:
            raise ConfigError("job.max_attempts must be 1 or more")
        if self.job.max_parallel < 1:
            raise ConfigError("job.max_parallel must be 1 or more")
        if "{title}" not in self.naming.pattern or "{year}" not in self.naming.pattern:
            raise ConfigError("naming.pattern must contain {title} and {year}")
        if not self.language.default:
            raise ConfigError("language.default must not be empty")
        return self

    # ---- helpers -------------------------------------------------------

    def active_profile(self) -> VideoProfile:
        """Return the video profile selected by video.profile."""
        if self.video.profile == "copy":
            return VideoProfile(encoder="copy", crf=0, preset=0, pixel_format="")
        return getattr(self.video, self.video.profile)

    def target_size_bytes(self) -> int:
        """Return the two-pass target size in bytes. 1 GB means 1e9 bytes."""
        if self.video.target_size_gb <= 0:
            return 0
        return int(self.video.target_size_gb * 1000000000)

    def to_dict(self) -> Dict[str, Any]:
        """Return the whole config as plain data, for 'mkv-transcode config'."""
        return _as_dict(self)

    def summary(self) -> List[str]:
        """Return short lines that describe the active settings."""
        profile = self.active_profile()
        lines = [
            "config:      %s" % (self.source_file or "<defaults>"),
            "incoming:    %s" % self.paths.incoming,
            "transcoded:  %s" % self.paths.transcoded,
            "processed:   %s" % self.paths.processed,
            "workdir:     %s" % self.paths.work,
            "logs:        %s" % self.paths.logs,
            "video:       %s (%s, crf=%s, preset=%s)"
            % (self.video.profile, profile.encoder, profile.crf, profile.preset),
            "audio:       %s -> %s (%d bps/channel, max %d channels)"
            % (self.audio.policy, self.audio.codec, self.audio.bitrate_per_channel_bps,
               self.audio.max_channels),
            "language:    default=%s, audio prefer=%s, subs prefer=%s"
            % (self.language.default, ",".join(self.language.audio_prefer) or "-",
               ",".join(self.language.subtitle_prefer) or "-"),
            "metadata:    %s" % self.metadata.provider,
            "naming:      %s" % self.naming.pattern,
            "source:      %s%s"
            % (self.output.source_action,
               " into %s" % self.output.move_source_to if self.output.move_source_to else ""),
        ]
        return lines


# ---- binding helpers --------------------------------------------------


def _as_dict(obj: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for f in fields(obj):
        value = getattr(obj, f.name)
        out[f.name] = _as_dict(value) if is_dataclass(value) else value
    return out


def _bind(target: Any, data: Dict[str, Any], prefix: str) -> None:
    """Copy values from a mapping into a dataclass instance."""
    known = {f.name for f in fields(target)}
    for key in sorted(set(data) - known):
        raise ConfigError("unknown key: %s%s%s" % (prefix, "." if prefix else "", key))
    for f in fields(target):
        if f.name not in data:
            continue
        raw = data[f.name]
        current = getattr(target, f.name)
        path = "%s.%s" % (prefix, f.name) if prefix else f.name
        if is_dataclass(current):
            if not isinstance(raw, dict):
                raise ConfigError("%s must be a table" % path)
            _bind(current, raw, path)
            continue
        if isinstance(raw, dict):
            raise ConfigError("%s must not be a table" % path)
        setattr(target, f.name, _coerce(current, raw, path))


def _sibling(base_dir: str, name: str, fallback: str) -> str:
    """Return <parent of base_dir>/<name>. Use fallback when there is no parent."""
    parent = os.path.dirname(os.path.abspath(base_dir).rstrip(os.sep))
    if not parent or parent == os.sep:
        return os.path.join(base_dir, fallback)
    return os.path.join(parent, name)


def _coerce(current: Any, raw: Any, path: str) -> Any:
    """Convert a raw TOML or environment value to the type of the default."""
    default = None if current is MISSING else current
    if isinstance(default, list):
        if isinstance(raw, str):
            raw = [part for part in raw.replace(",", " ").split()]
        if not isinstance(raw, list):
            raise ConfigError("%s must be a list" % path)
        return [str(item) for item in raw]
    if isinstance(default, bool):
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, str) and raw.lower() in ("1", "true", "yes", "on"):
            return True
        if isinstance(raw, str) and raw.lower() in ("0", "false", "no", "off"):
            return False
        raise ConfigError("%s must be true or false" % path)
    if isinstance(default, int) and not isinstance(default, bool):
        try:
            return int(raw)
        except (TypeError, ValueError):
            raise ConfigError("%s must be an integer" % path)
    if isinstance(default, float):
        try:
            return float(raw)
        except (TypeError, ValueError):
            raise ConfigError("%s must be a number" % path)
    if isinstance(default, str) or default is None:
        return raw if isinstance(raw, str) else str(raw)
    return raw


def _one_of(value: str, allowed: tuple, path: str) -> None:
    if value not in allowed:
        raise ConfigError("%s must be one of: %s" % (path, ", ".join(allowed)))
