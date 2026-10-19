"""Build the ffmpeg command line for one source file.

The builder maps streams by index. That keeps the output predictable when a
disc carries extra angles, commentary tracks or a cover image.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List, Optional

from ..config import Config
from ..errors import TranscodeError
from ..probe import IMAGE_SUBTITLES, MediaInfo, Stream

NULL_DEVICE = "/dev/null"
MAX_OPUS_BITRATE = 510000
MAX_AAC_BITRATE = 320000
# The user-facing codec name mapped to the ffmpeg encoder. libopus gives more
# control than the native Opus encoder.
AUDIO_ENCODERS = {"opus": "libopus", "aac": "aac", "libopus": "libopus"}
# Codecs that no consumer plays without HDMI passthrough.
LOSSLESS_AUDIO = ("truehd", "dts", "dtshd_ma", "dtshd_hsr", "pcm_s16le", "pcm_s24le", "alac")
# ffmpeg's libopus encoder rejects these layouts: 2.1, 3.1, 5.1(side), 7.1(wide)
# and 7.1(wide-side). It accepts the ones listed here.
OPUS_LAYOUTS = frozenset((
    "mono", "stereo", "3.0", "quad", "5.0", "5.0(side)", "5.1", "6.1", "6.1(side)", "7.1",
))
# Layout chosen from the channel count when the source layout is not usable.
LAYOUT_BY_CHANNELS = {
    1: "mono", 2: "stereo", 3: "3.0", 4: "quad",
    5: "5.0", 6: "5.1", 7: "6.1", 8: "7.1",
}


@dataclass
class Decision:
    """What the pipeline does with one input stream."""

    kind: str                 # video | audio | subtitle
    source_index: int
    position: int = 0         # index within its stream type, used by -map
    out_index: int = 0
    action: str = ""          # copy | encode | drop
    codec: str = ""
    reason: str = ""
    bitrate: int = 0
    channels: int = 0
    downmix_to: int = 0
    audio_filter: str = ""
    layout: str = ""
    default: bool = False
    language: Optional[str] = None

    def describe(self) -> str:
        language = self.language or "und"
        extra = ""
        if self.kind == "audio" and self.action == "encode":
            extra = " %d kbps, %d ch" % (self.bitrate // 1000, self.channels)
        elif self.kind == "audio" and self.action == "copy":
            extra = " (copy)"
        return "%-8s #%d [%s] %s%s" % (
            self.kind, self.source_index, language,
            self.action + ":" + (self.codec or "-"), extra,
        )


@dataclass
class Plan:
    """A complete transcode plan for one file."""

    input_path: str
    output_path: str
    duration: float
    video: Optional[Decision]
    audio: List[Decision]
    subtitles: List[Decision]
    filters: List[str]
    color_args: List[str]
    passes: int
    video_bitrate: int
    profile: str
    encoder: str
    notes: List[str] = field(default_factory=list)
    quality_args: List[str] = field(default_factory=list)
    preset_args: List[str] = field(default_factory=list)
    pixel_format: str = ""
    threads: int = 0
    extra_args: List[str] = field(default_factory=list)
    audio_extra_args: List[str] = field(default_factory=list)
    title_tag: str = ""
    imdb_id: str = ""
    keep_metadata: bool = True
    chapters: bool = False
    default_language: str = "eng"

    def command(self, pass_index: int = 0) -> List[str]:
        """Return the ffmpeg argument list for one pass."""
        args = [
            "-hide_banner",
            "-nostdin",
            "-loglevel", "warning",
            "-y",
            "-i", self.input_path,
        ]
        args += self._maps()
        args += self._codec_args(pass_index)
        if self.filters:
            args += ["-vf", ",".join(self.filters)]
        args += self.color_args
        args += self._metadata_args()
        if self.passes > 1:
            args += ["-pass", str(pass_index + 1), "-passlogfile", self._passlog()]
            if pass_index == 0:
                args += ["-an", "-sn", "-f", "null", NULL_DEVICE]
                return args
        # Matroska muxer options must follow '-f matroska'.
        args += ["-progress", "pipe:1", "-nostats", "-f", "matroska"]
        args += [self.output_path]
        return args

    def full_command(self, pass_index: int = 0) -> str:
        """Return the command as a printable string."""
        import shlex

        return " ".join(shlex.quote(part) for part in ["ffmpeg"] + self.command(pass_index))

    def _maps(self) -> List[str]:
        args: List[str] = []
        if self.video:
            args += ["-map", "0:v:%d" % self.video.position]
        for decision in self.audio:
            args += ["-map", "0:a:%d" % decision.position]
        for decision in self.subtitles:
            args += ["-map", "0:s:%d" % decision.position]
        args += ["-dn"]
        return args

    def _codec_args(self, pass_index: int) -> List[str]:
        args: List[str] = []
        if self.video:
            args += ["-c:v:0", self.encoder]
            # The main feature is always the default video track.
            args += ["-disposition:v:0", "default"]
        for decision in self.audio:
            args += ["-c:a:%d" % decision.out_index, "copy" if decision.action == "copy" else decision.codec]
        for decision in self.subtitles:
            args += ["-c:s:%d" % decision.out_index, "copy"]
        if self.video and self.video.action == "encode":
            args += self._video_quality_args(pass_index)
        for decision in self.audio:
            if decision.action == "encode":
                args += ["-b:a:%d" % decision.out_index, str(decision.bitrate)]
                if decision.downmix_to:
                    args += ["-ac:a:%d" % decision.out_index, str(decision.downmix_to)]
                if decision.layout:
                    args += ["-channel_layout:a:%d" % decision.out_index, decision.layout]
                if decision.audio_filter:
                    args += ["-af:a:%d" % decision.out_index, decision.audio_filter]
                for extra in self.audio_extra_args:
                    args += _split_extra(extra)
        for decision in self.audio + self.subtitles:
            if decision.default:
                args += ["-disposition:%s:%d" % (_kind_letter(decision.kind), decision.out_index), "default"]
            if not decision.language:
                # Tag the track so players group it under the default language.
                args += ["-metadata:s:%s:%d" % (_kind_letter(decision.kind), decision.out_index),
                         "language=%s" % self.default_language]
        return args

    def _video_quality_args(self, pass_index: int) -> List[str]:
        args: List[str] = []
        if self.passes > 1:
            args += ["-b:v", str(self.video_bitrate)]
        else:
            args += self.quality_args
        if self.preset_args:
            args += self.preset_args
        if self.pixel_format:
            args += ["-pix_fmt", self.pixel_format]
        if self.threads:
            args += ["-threads", str(self.threads)]
        for extra in self.extra_args:
            args += _split_extra(extra)
        return args

    def _metadata_args(self) -> List[str]:
        args: List[str] = []
        if self.keep_metadata:
            args += ["-map_metadata", "0"]
        if self.chapters:
            args += ["-map_chapters", "0"]
        if self.title_tag:
            args += ["-metadata", "title=%s" % self.title_tag]
        if self.imdb_id:
            args += ["-metadata", "IMDB=%s" % self.imdb_id]
        args += ["-max_muxing_queue_size", "4096"]
        return args

    def _passlog(self) -> str:
        directory = os.path.dirname(self.output_path) or "."
        return os.path.join(directory, "ffmpeg-passlog")

    def summary(self) -> List[str]:
        """Human readable lines that describe the plan."""
        lines = ["video: %s" % (self.video.describe() if self.video else "none")]
        lines += ["audio: %s" % decision.describe() for decision in self.audio]
        lines += ["subs:   %s" % decision.describe() for decision in self.subtitles]
        if self.filters:
            lines.append("filters: %s" % ",".join(self.filters))
        lines.append("quality: %s passes=%d %s" % (
            self.profile, self.passes,
            "bitrate=%d kbps" % (self.video_bitrate // 1000) if self.passes > 1 else "crf",
        ))
        lines += self.notes
        return lines


def _kind_letter(kind: str) -> str:
    return {"video": "v", "audio": "a", "subtitle": "s"}[kind]


def _split_extra(extra: str) -> List[str]:
    """Turn 'x265-params=log-level=none' into ['-x265-params', 'log-level=none']."""
    if "=" not in extra:
        return ["-" + extra]
    key, value = extra.split("=", 1)
    return ["-" + key, value]


def build_plan(
    config: Config,
    info: MediaInfo,
    output_path: str,
    title_tag: str = "",
    imdb_id: str = "",
) -> Plan:
    """Decide what ffmpeg must do for one source file."""
    notes: List[str] = []
    video = _decide_video(config, info, notes)
    audio = _decide_audio(config, info, notes)
    subtitles = _decide_subtitles(config, info, notes)

    filters, color_args = _video_filters(config, info, video, subtitles, notes)
    profile = config.active_profile()
    passes = 1
    video_bitrate = 0
    if config.video.target_size_gb > 0 and video and video.action == "encode":
        passes = 2
        video_bitrate = _target_video_bitrate(config, info, audio)

    plan = Plan(
        input_path=info.path,
        output_path=output_path,
        duration=info.duration,
        video=video,
        audio=audio,
        subtitles=subtitles,
        filters=filters,
        color_args=color_args,
        passes=passes,
        video_bitrate=video_bitrate,
        profile=config.video.profile,
        encoder=profile.encoder,
        notes=notes,
    )
    _attach_profile(plan, config, profile)
    plan.title_tag = title_tag
    plan.imdb_id = imdb_id
    plan.keep_metadata = config.video.keep_metadata
    plan.chapters = bool(info.chapters) and config.video.keep_chapters
    plan.default_language = config.language.default
    return plan


def _attach_profile(plan: Plan, config: Config, profile) -> None:
    """Copy the profile settings onto the plan."""
    if profile.encoder == "copy":
        plan.quality_args = []
    elif profile.quality_args:
        plan.quality_args = list(profile.quality_args)
    else:
        plan.quality_args = ["-crf", str(profile.crf)]
    plan.preset_args = []
    if profile.preset not in (None, "", 0):
        plan.preset_args = ["-preset", str(profile.preset)]
    plan.pixel_format = profile.pixel_format
    plan.threads = config.video.threads
    plan.extra_args = list(profile.extra_args)
    plan.audio_extra_args = list(config.audio.extra_args)


def _decide_video(config: Config, info: MediaInfo, notes: List[str]) -> Optional[Decision]:
    try:
        video = info.primary_video()
    except Exception:
        return None
    others = [s for s in info.videos() if s.index != video.index]
    if others:
        notes.append("dropped %d extra video stream(s): %s" % (
            len(others), ", ".join("#%d %sx%s %s" % (s.index, s.width, s.height, s.codec_name) for s in others)))
    if config.video.profile == "copy":
        return Decision("video", video.index, info.position_in_type(video), 0, "copy",
                        video.codec_name, "video.profile=copy")
    return Decision("video", video.index, info.position_in_type(video), 0, "encode",
                    config.active_profile().encoder, "profile=%s" % config.video.profile)


def _decide_audio(config: Config, info: MediaInfo, notes: List[str]) -> List[Decision]:
    """Pick the audio tracks and decide copy or encode for each one."""
    streams = info.audios()
    if not streams:
        notes.append("no audio streams found")
        return []

    kept = streams
    if config.audio.policy != "copy" and config.language.audio_keep:
        wanted = [s for s in streams if (s.language or "") in set(config.language.audio_keep)]
        if wanted:
            dropped = len(streams) - len(wanted)
            if dropped:
                notes.append("dropped %d audio track(s) outside language.audio_keep" % dropped)
            kept = wanted

    if not config.audio.keep_commentary:
        wanted = [s for s in kept if "commentary" not in (s.title or "").lower()]
        if wanted and len(wanted) < len(kept):
            notes.append("dropped %d commentary track(s)" % (len(kept) - len(wanted)))
            kept = wanted

    # Order the tracks so the preferred language is first. That makes the
    # default flag simple and keeps Jellyfin on the English track.
    kept = _order_by_language(kept, config.language.audio_prefer)

    decisions: List[Decision] = []
    for out_index, stream in enumerate(kept):
        action, codec, reason = _audio_action(config, stream)
        channels = stream.channels or 2
        bitrate = 0
        downmix_to = 0
        if action == "encode":
            codec = AUDIO_ENCODERS.get(codec, codec)
            if config.audio.downmix == "auto" and channels > config.audio.max_channels:
                notes.append("audio #%d downmixed from %d to %d channels" % (
                    stream.index, channels, config.audio.max_channels))
                downmix_to = config.audio.max_channels
                channels = config.audio.max_channels
            bitrate = _audio_bitrate(config, codec, channels)
            layout = _opus_layout(config, stream, channels)
            if layout:
                notes.append("audio #%d layout %s -> %s for the Opus encoder" % (
                    stream.index, stream.channel_layout or "unknown", layout))
        else:
            layout = ""
        audio_filter = ""
        if action == "encode" and config.audio.loudnorm:
            audio_filter = "loudnorm=I=-23:TP=-2:LRA=11"
        decisions.append(Decision(
            kind="audio",
            source_index=stream.index,
            position=info.position_in_type(stream),
            out_index=out_index,
            action=action,
            codec=codec if action == "encode" else stream.codec_name,
            reason=reason,
            bitrate=bitrate,
            channels=channels,
            downmix_to=downmix_to,
            audio_filter=audio_filter,
            layout=layout,
            default=out_index == 0,
            language=stream.language,
        ))
    return decisions


def _opus_layout(config: Config, stream: Stream, channels: int) -> str:
    """Return a layout libopus accepts, or an empty string to keep the source."""
    if config.audio.codec != "opus":
        return ""
    source = (stream.channel_layout or "").strip()
    if source in OPUS_LAYOUTS:
        return ""
    return LAYOUT_BY_CHANNELS.get(channels, "stereo")


def _audio_action(config: Config, stream: Stream):
    codec = (stream.codec_name or "").lower()
    if config.audio.policy == "copy":
        return "copy", stream.codec_name, "audio.policy=copy"
    if config.audio.policy == "transcode":
        return "encode", config.audio.codec, "audio.policy=transcode"
    if codec in LOSSLESS_AUDIO:
        return "encode", config.audio.codec, "%s has no consumer support" % codec
    if codec in set(config.audio.copy_codecs):
        channels = stream.channels or 2
        if channels > config.audio.max_channels:
            return "encode", config.audio.codec, "%d channels exceeds audio.max_channels" % channels
        return "copy", stream.codec_name, "%s is a compatible codec" % codec
    return "encode", config.audio.codec, "%s is not in audio.copy_codecs" % codec


def _audio_bitrate(config: Config, codec: str, channels: int) -> int:
    cap = MAX_AAC_BITRATE if codec == "aac" else MAX_OPUS_BITRATE
    value = config.audio.bitrate_per_channel_bps * max(1, channels)
    return int(max(config.audio.min_bitrate_bps, min(value, cap)))


def _order_by_language(streams: List[Stream], prefer: List[str]) -> List[Stream]:
    """Return the streams with preferred languages first, order kept otherwise."""
    if not prefer:
        return streams
    preferred = [s for s in streams if (s.language or "") in set(prefer)]
    rest = [s for s in streams if (s.language or "") not in set(prefer)]
    if not preferred:
        return streams
    return preferred + rest


def _decide_subtitles(config: Config, info: MediaInfo, notes: List[str]) -> List[Decision]:
    if not config.subtitles.copy:
        notes.append("subtitles dropped: subtitles.copy=false")
        return []
    streams = info.subtitles()
    if not streams:
        return []
    kept = streams
    if config.subtitles.forced_only:
        forced = [s for s in kept if s.forced]
        if forced:
            notes.append("kept only %d forced subtitle track(s)" % len(forced))
            kept = forced
    if config.language.subtitle_keep:
        wanted = [s for s in streams if (s.language or "") in set(config.language.subtitle_keep)]
        if wanted:
            if len(wanted) < len(streams):
                notes.append("dropped %d subtitle track(s) outside language.subtitle_keep"
                             % (len(streams) - len(wanted)))
            kept = wanted
    if not config.subtitles.keep_image_tracks:
        text = [s for s in kept if s.codec_name not in IMAGE_SUBTITLES]
        if text:
            if len(text) < len(kept):
                notes.append("dropped %d image subtitle track(s)" % (len(kept) - len(text)))
            kept = text
    decisions = []
    preferred = set(config.language.subtitle_prefer)
    for out_index, stream in enumerate(kept):
        if config.subtitles.default_track == "source":
            is_default = bool(stream.default) and not any(d.default for d in decisions)
        elif config.subtitles.default_track == "none":
            is_default = False
        else:
            is_default = bool(preferred) and (stream.language or "") in preferred and not any(
                d.default for d in decisions)
        decisions.append(Decision(
            kind="subtitle",
            source_index=stream.index,
            position=info.position_in_type(stream),
            out_index=out_index,
            action="copy",
            codec=stream.codec_name,
            reason="subtitle copy",
            default=is_default,
            language=stream.language,
        ))
    return decisions


def _video_filters(config: Config, info: MediaInfo, video: Optional[Decision],
                   subtitles: List[Decision], notes: List[str]):
    """Return the video filter chain and the colour tag arguments."""
    filters: List[str] = []
    color_args: List[str] = []
    if not video or video.action == "copy":
        return filters, color_args

    profile = config.active_profile()
    target_pix = profile.pixel_format or "yuv420p"
    hdr = info.is_hdr()
    interlaced = info.is_interlaced()

    if config.video.deinterlace == "always" or (config.video.deinterlace == "auto" and interlaced):
        filters.append("bwdif=mode=send_frame:parity=auto:deint=interlaced")
        notes.append("deinterlaced with bwdif (field_order=%s)" % (info.primary_video().field_order or "?"))

    tonemap = config.video.tonemap == "tonemap" or (
        config.video.tonemap == "auto" and hdr and "10le" not in target_pix
    )
    if hdr and tonemap:
        filters.extend(_tonemap_chain(target_pix))
        color_args = ["-colorspace", "bt709", "-color_primaries", "bt709",
                      "-color_trc", "bt709", "-color_range", "tv"]
        notes.append("HDR source tone mapped to SDR")
    elif hdr:
        source = info.primary_video()
        color_args = _color_passthrough(source)
        notes.append("HDR metadata kept")
    else:
        source = info.primary_video()
        color_args = _color_passthrough(source)

    if config.video.max_width > 0:
        width = config.video.max_width
        current = info.primary_video().width or 0
        if current > width:
            filters.append("scale=w=%d:h=-2:flags=lanczos" % width)
            notes.append("scaled from %d to %d pixels wide" % (current, width))

    if config.subtitles.burn_in and subtitles:
        burn = _burn_in_filter(info, subtitles)
        if burn:
            filters.append(burn)
            notes.append("subtitle #%d burned in" % subtitles[0].source_index)

    return filters, color_args


def _tonemap_chain(target_pix: str) -> List[str]:
    """The zscale plus tonemap chain that maps HDR10 to SDR."""
    return [
        "zscale=t=linear:npl=100",
        "format=gbrpf32le",
        "zscale=p=bt709",
        "tonemap=tonemap=hable:desat=0",
        "zscale=t=bt709:m=bt709:r=tv",
        "format=%s" % target_pix,
    ]


def _color_passthrough(source: Stream) -> List[str]:
    args: List[str] = []
    if source.color_space:
        args += ["-colorspace", source.color_space]
    if source.color_primaries:
        args += ["-color_primaries", source.color_primaries]
    if source.color_transfer:
        args += ["-color_trc", source.color_transfer]
    return args


def _burn_in_filter(info: MediaInfo, subtitles: List[Decision]) -> str:
    """Build a subtitles filter. The input path is quoted for the filter parser."""
    stream = next((s for s in subtitles), None)
    if not stream:
        return ""
    escaped = info.path.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    return "subtitles=filename='%s':chapters=0:stream_index=%d" % (escaped, stream.position)


def _target_video_bitrate(config: Config, info: MediaInfo, audio: List[Decision]) -> int:
    """Work out the video bit rate for a two-pass target size."""
    duration = info.duration or 0
    if duration <= 0:
        raise TranscodeError("cannot compute a target bit rate: duration is unknown")
    target_bytes = config.target_size_bytes()
    total_bits = target_bytes * 8
    audio_bits = 0
    for decision in audio:
        rate = decision.bitrate or _source_audio_rate(info, decision.source_index)
        audio_bits += rate * duration
    overhead = int(total_bits * 0.01)
    video_bits = total_bits - audio_bits - overhead
    if video_bits <= 0:
        raise TranscodeError(
            "target size %.2f GB is too small for %.1f minutes of audio"
            % (config.video.target_size_gb, duration / 60)
        )
    return int(video_bits / duration)


def _source_audio_rate(info: MediaInfo, source_index: int) -> int:
    for stream in info.audios():
        if stream.index == source_index:
            return stream.bit_rate or 128000
    return 128000
