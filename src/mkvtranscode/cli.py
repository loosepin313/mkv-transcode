"""Command line interface.

Run 'mkv-transcode --help' for the command list. Every command accepts
--config. Most commands also accept the common overrides --incoming,
--transcoded, --profile, --crf and --dry-run.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import fields, is_dataclass
from typing import List, Optional

from . import __version__
from .config import Config
from .doctor import exit_code, run_checks
from .errors import ConfigError, MkvTranscodeError
from .log import get_logger, setup
from .metadata import build_provider
from .naming import parse_filename
from .pipeline import Pipeline
from .scan import is_ready, list_candidates
from .state import State
from .util import human_duration, human_size
from .watcher import Watcher

EXIT_OK, EXIT_FAIL, EXIT_USAGE = 0, 1, 2
log = get_logger("cli")

# The common flags use SUPPRESS so a subcommand cannot reset them. This table
# supplies the missing attributes after parsing.
COMMON_DEFAULTS = {
    "config": None, "incoming": None, "transcoded": None, "processed": None,
    "work": None, "profile": None,
    "crf": None, "preset": None, "provider": None, "target_size_gb": None,
    "audio_codec": None, "audio_policy": None, "log_level": None, "dry_run": False,
}


def normalize_flags(args: argparse.Namespace) -> argparse.Namespace:
    """Fill the common attributes that the user did not set."""
    for name, value in COMMON_DEFAULTS.items():
        if not hasattr(args, name):
            setattr(args, name, value)
    return args


def _add_common_flags(parser: argparse.ArgumentParser) -> None:
    """Add the shared flags. SUPPRESS keeps a subcommand from resetting them."""
    parser.add_argument("-c", "--config", default=argparse.SUPPRESS,
                        help="path to the TOML config file")
    parser.add_argument("--incoming", default=argparse.SUPPRESS, help="override paths.incoming")
    parser.add_argument("--transcoded", default=argparse.SUPPRESS, help="override paths.transcoded")
    parser.add_argument("--processed", default=argparse.SUPPRESS, help="override paths.processed")
    parser.add_argument("--work", default=argparse.SUPPRESS, help="override paths.work")
    parser.add_argument("--profile", choices=["av1", "hevc", "h264", "copy"],
                        default=argparse.SUPPRESS, help="override video.profile")
    parser.add_argument("--crf", type=int, default=argparse.SUPPRESS,
                        help="override the CRF of the active profile")
    parser.add_argument("--preset", default=argparse.SUPPRESS,
                        help="override the preset of the active profile")
    parser.add_argument("--provider", choices=["imdb", "imdbpy", "filename", "none"],
                        default=argparse.SUPPRESS, help="override metadata.provider")
    parser.add_argument("--target-size-gb", type=float, default=argparse.SUPPRESS,
                        help="override video.target_size_gb and use a two-pass encode")
    parser.add_argument("--audio-codec", choices=["opus", "aac"], default=argparse.SUPPRESS,
                        help="override audio.codec")
    parser.add_argument("--audio-policy", choices=["copy", "transcode", "smart"],
                        default=argparse.SUPPRESS, help="override audio.policy")
    parser.add_argument("--log-level", choices=["debug", "info", "warning", "error"],
                        default=argparse.SUPPRESS)
    parser.add_argument("--dry-run", action="store_true", default=argparse.SUPPRESS,
                        help="print the plan and change nothing")


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    _add_common_flags(common)

    parser = argparse.ArgumentParser(
        prog="mkv-transcode",
        description="Transcode Matroska rips into AV1 or HEVC Matroska files with IMDb based names.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        parents=[common],
        epilog=(
            "Examples:\n"
            "  mkv-transcode doctor\n"
            "  mkv-transcode imdb fetch\n"
            "  mkv-transcode plan incoming/Alien.1979.1080p.BluRay.mkv\n"
            "  mkv-transcode once\n"
            "  mkv-transcode run\n"
        ),
    )
    parser.add_argument("--version", action="version", version="mkv-transcode %s" % __version__)
    sub = parser.add_subparsers(dest="command")

    def add(name, **kwargs):
        """Create a subcommand that also accepts the common flags."""
        return sub.add_parser(name, parents=[common], **kwargs)

    run = add("run", help="watch the incoming directory and process files (daemon mode)")
    run.add_argument("--once", action="store_true", help="process what is ready, then exit")
    run.add_argument("--wait", type=float, default=0.0,
                     help="with --once, keep waiting for copying files up to N seconds")

    once = add("once", help="scan incoming once, process every ready file, then exit")
    once.add_argument("--wait", type=float, default=0.0, help="wait up to N seconds for copying files")

    process = add("process", help="process specific files now")
    process.add_argument("files", nargs="+", help="source files")
    process.add_argument("--force", action="store_true", help="reprocess a file already in the state file")

    plan = add("plan", help="print the ffmpeg plan for one file and change nothing")
    plan.add_argument("file")
    plan.add_argument("--json", action="store_true", help="print machine readable output")

    add("list", help="list the files in incoming and their state")
    add("status", help="show the job history")

    imdb = add("imdb", help="manage the local IMDb index")
    imdb_sub = imdb.add_subparsers(dest="imdb_command")
    fetch = imdb_sub.add_parser("fetch", help="download the IMDb datasets and build the index")
    fetch.add_argument("--from-file", help="use a local title.basics.tsv.gz instead of downloading")
    fetch.add_argument("--akas-file", help="use a local title.akas.tsv.gz instead of downloading")
    lookup = imdb_sub.add_parser("lookup", help="look up one IMDb id")
    lookup.add_argument("imdb_id")
    search = imdb_sub.add_parser("search", help="search titles by name")
    search.add_argument("title")
    search.add_argument("--year", type=int)
    imdb_sub.add_parser("status", help="show the index state")
    imdb_sub.add_parser("remove", help="delete the local index and the downloaded files")

    add("doctor", help="check the machine and the configuration")

    web = add("status-web", help="serve the transcode status page over HTTP")
    web.add_argument("--host", default="127.0.0.1",
                     help="address to bind. Use 0.0.0.0 to reach the page from your LAN")
    web.add_argument("--port", type=int, default=8090, help="port to listen on")
    web.add_argument("--interval", type=float, default=5.0,
                     help="seconds between browser refreshes")
    web.add_argument("--recent", type=int, default=80, help="jobs to keep in the list")
    add("config", help="print the effective configuration")
    add("config-file", help="print the default configuration as TOML")

    init = add("init", help="create directories and a config file")
    init.add_argument("--path", default="mkv-transcode.toml", help="config file to write")
    init.add_argument("--make-dirs", action="store_true",
                      help="also create the folders under the configured root")

    return parser


def apply_overrides(config: Config, args: argparse.Namespace) -> Config:
    """Apply command line overrides on top of the loaded config."""
    if args.incoming:
        config.paths.incoming = args.incoming
    if args.transcoded:
        config.paths.transcoded = args.transcoded
    if args.processed:
        config.paths.processed = args.processed
    if args.work:
        config.paths.work = args.work
    if args.profile:
        config.video.profile = args.profile
    if args.provider:
        config.metadata.provider = args.provider
    if args.audio_codec:
        config.audio.codec = args.audio_codec
    if args.audio_policy:
        config.audio.policy = args.audio_policy
    if args.target_size_gb is not None:
        config.video.target_size_gb = args.target_size_gb
    if args.log_level:
        config.job.log_level = args.log_level
    if args.crf is not None:
        config.active_profile().crf = args.crf
    if args.preset is not None:
        profile = config.active_profile()
        profile.preset = int(args.preset) if str(args.preset).isdigit() else args.preset
    config.resolve().validate()
    return config


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = normalize_flags(parser.parse_args(argv))
    if not args.command:
        parser.print_help()
        return EXIT_USAGE
    try:
        config = Config.discover() if args.command != "config-file" else Config()
        if args.command != "config-file":
            apply_overrides(config, args)
    except ConfigError as exc:
        print("config error: %s" % exc, file=sys.stderr)
        return EXIT_USAGE

    setup(config.job.log_level if args.command != "config-file" else "info")

    try:
        if args.command == "run":
            return _cmd_run(config, args)
        if args.command == "once":
            return _cmd_run(config, argparse.Namespace(once=True, wait=args.wait, dry_run=args.dry_run))
        if args.command == "process":
            return _cmd_process(config, args)
        if args.command == "plan":
            return _cmd_plan(config, args)
        if args.command == "list":
            return _cmd_list(config)
        if args.command == "status":
            return _cmd_status(config)
        if args.command == "imdb":
            return _cmd_imdb(config, args)
        if args.command == "doctor":
            return _cmd_doctor(config)
        if args.command == "status-web":
            return _cmd_status_web(config, args)
        if args.command == "config":
            return _cmd_config(config)
        if args.command == "config-file":
            print(default_config_toml())
            return EXIT_OK
        if args.command == "init":
            return _cmd_init(config, args)
    except MkvTranscodeError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return EXIT_FAIL
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    return EXIT_USAGE


# ---- commands ----------------------------------------------------------


def _cmd_run(config: Config, args: argparse.Namespace) -> int:
    state = State(os.path.join(config.paths.transcoded, ".state.sqlite"))
    watcher = Watcher(config, state)
    try:
        return watcher.run(once=args.once, dry_run=args.dry_run, wait_seconds=args.wait)
    finally:
        state.close()


def _cmd_process(config: Config, args: argparse.Namespace) -> int:
    state = State(os.path.join(config.paths.transcoded, ".state.sqlite"))
    pipeline = Pipeline(config, state)
    failures = 0
    try:
        for path in args.files:
            outcome = pipeline.process(path, dry_run=args.dry_run, force=args.force)
            if outcome.status == "failed":
                failures += 1
    finally:
        state.close()
    return EXIT_FAIL if failures else EXIT_OK


def _cmd_plan(config: Config, args: argparse.Namespace) -> int:
    from .metadata import resolve_title
    from .probe import ffprobe
    from .transcode import build_plan

    path = os.path.abspath(args.file)
    info = ffprobe(path)
    guess = parse_filename(path)
    title = resolve_title(config, guess, build_provider(config))
    work = os.path.join(config.paths.work, "plan-only." + config.output.container)
    plan = build_plan(config, info, work, title_tag=title.title, imdb_id=title.imdb_id or "")
    if args.json:
        payload = {
            "input": path,
            "title": title.title,
            "year": title.year,
            "imdb_id": title.imdb_id,
            "command": plan.command(0),
            "streams": [
                {"index": s.index, "type": s.codec_type, "codec": s.codec_name,
                 "language": s.language, "channels": s.channels,
                 "width": s.width, "height": s.height}
                for s in info.streams
            ],
        }
        print(json.dumps(payload, indent=2))
        return EXIT_OK
    print("input:   %s" % path)
    print("named:   %s" % title.summary())
    print("source:  %s, %s, %s" % (info.format_name, human_size(info.size), human_duration(info.duration)))
    for stream in info.streams:
        print("  stream #%d %-8s %s" % (stream.index, stream.codec_type, stream.label))
    print("")
    for line in plan.summary():
        print(line)
    print("")
    print("command:")
    print("  " + plan.full_command(0))
    if plan.passes > 1:
        print("  " + plan.full_command(1))
    return EXIT_OK


def _cmd_list(config: Config) -> int:
    candidates = list_candidates(config)
    if not candidates:
        print("no files in %s" % config.paths.incoming)
        return EXIT_OK
    state = State(os.path.join(config.paths.transcoded, ".state.sqlite"))
    try:
        for candidate in candidates:
            record = state.get_job(candidate.path)
            ready = "ready" if is_ready(candidate.path, config) else "copying"
            done = record.status if record else "-"
            print("%-9s %-8s %10s  %s" % (ready, done, human_size(candidate.size),
                                          os.path.relpath(candidate.path, config.paths.incoming)))
    finally:
        state.close()
    return EXIT_OK


def _cmd_status(config: Config) -> int:
    path = os.path.join(config.paths.transcoded, ".state.sqlite")
    if not os.path.isfile(path):
        print("no state file yet: %s" % path)
        return EXIT_OK
    state = State(path)
    try:
        counts = state.counts()
        print("jobs: " + (", ".join("%s=%d" % item for item in sorted(counts.items())) or "none"))
        print("")
        print("%-8s %-10s %-10s %s" % ("status", "size", "time", "title"))
        for record in state.recent_jobs(25):
            title = record.title or os.path.basename(record.input)
            if record.year:
                title = "%s (%s)" % (title, record.year)
            print("%-8s %-10s %-10s %s" % (
                record.status,
                human_size(record.output_size) if record.output_size else "-",
                human_duration(record.seconds) if record.seconds else "-",
                title,
            ))
            if record.message:
                print("         message: %s" % record.message)
    finally:
        state.close()
    return EXIT_OK


def _cmd_status_web(config: Config, args: argparse.Namespace) -> int:
    """Serve the status page. The server reads files and writes nothing."""
    from .status.server import serve
    return serve(config, host=args.host, port=args.port, interval=args.interval,
                 recent=args.recent)


def _cmd_imdb(config: Config, args: argparse.Namespace) -> int:
    from .metadata.imdb_dataset import ImdbDatasetProvider, remove_dataset

    provider = ImdbDatasetProvider(
        data_dir=config.paths.imdb_data,
        title_types=config.metadata.title_types,
        include_akas=config.metadata.include_akas,
        akas_countries=config.metadata.akas_countries,
    )
    command = args.imdb_command
    if not command:
        print("imdb needs a subcommand: fetch, lookup, search, status, remove")
        return EXIT_USAGE
    if command == "status":
        print("data dir: %s" % config.paths.imdb_data)
        print(provider.status())
        return EXIT_OK
    if command == "fetch":
        def say(message: str) -> None:
            print(message, flush=True)

        provider.fetch(progress=say, basics_file=args.from_file, akas_file=args.akas_file)
        return EXIT_OK
    if command == "remove":
        removed = remove_dataset(config.paths.imdb_data)
        print("removed" if removed else "nothing to remove")
        return EXIT_OK
    if not provider.available():
        print("IMDb index is missing. Run: mkv-transcode imdb fetch", file=sys.stderr)
        return EXIT_FAIL
    if command == "lookup":
        found = provider.lookup_id(args.imdb_id)
        print(found.summary() if found else "not found: %s" % args.imdb_id)
        return EXIT_OK if found else EXIT_FAIL
    if command == "search":
        found = provider.search(args.title, args.year)
        print(found.summary() if found else "no match for %r" % args.title)
        return EXIT_OK if found else EXIT_FAIL
    return EXIT_USAGE


def _cmd_doctor(config: Config) -> int:
    checks = run_checks(config)
    for check in checks:
        print(check.line())
    code = exit_code(checks)
    print("")
    print("result: %s" % ("ready" if code == 0 else "problems found"))
    for line in config.summary():
        print(line)
    return code


def _cmd_config(config: Config) -> int:
    print(json.dumps(config.to_dict(), indent=2, sort_keys=True))
    return EXIT_OK


def _cmd_init(config: Config, args: argparse.Namespace) -> int:
    path = os.path.abspath(args.path)
    if os.path.exists(path):
        print("refusing to overwrite %s" % path, file=sys.stderr)
        return EXIT_FAIL
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(default_config_toml())
    print("wrote %s" % path)
    if args.make_dirs:
        # Read the file just written. That anchors every relative folder to
        # the config file, so a checkout keeps its data in its own folder.
        fresh = Config.load(path)
        for directory in (fresh.paths.incoming, fresh.paths.transcoded,
                          fresh.paths.processed, fresh.paths.work,
                          fresh.paths.imdb_data):
            os.makedirs(directory, exist_ok=True)
            print("created %s" % directory)
    return EXIT_OK


# ---- default config text ----------------------------------------------

_SECTION_COMMENTS = {
    "paths": "Directories. A relative path sits next to this config file. "
             "incoming: the rips. transcoded: the library. processed: the source "
             "after a good run. work: partial output. logs: job logs. "
             "imdb: the IMDb index.",
    "watch": "How the watcher finds complete files.",
    "video": "Video settings. profile picks av1, hevc, h264 or copy.",
    "audio": "Audio policy. 'smart' copies compatible tracks and re-encodes the rest.",
    "subtitles": "Subtitle handling.",
    "language": "Language rules. ffmpeg uses ISO 639-2/T codes such as 'eng'.",
    "naming": "Output layout under paths.transcoded.",
    "metadata": "IMDb lookup settings.",
    "output": "What to do with the finished file and the source file.",
    "job": "Runtime limits and logging.",
}


# The example file names every folder. The code leaves these three empty and
# derives them from 'transcoded'. Write "" in your own config to do that.
_EXAMPLE_PATHS = {
    "work": "workdir",
    "logs": "transcoded/.logs",
    "processed": "processed",
}


def default_config_toml() -> str:
    """Render the default configuration as TOML."""
    config = Config()
    lines = [
        "# mkv-transcode configuration.",
        "# Copy this file to /etc/mkv-transcode/config.toml or to ./mkv-transcode.toml.",
        "# Every key is optional. See docs/CONFIGURATION.md for the full list.",
        "",
    ]
    for section in ("paths", "watch", "video", "audio", "subtitles", "language",
                    "naming", "metadata", "output", "job"):
        target = getattr(config, section)
        lines.append("# " + _SECTION_COMMENTS[section])
        lines.append("[%s]" % section)
        for f in fields(target):
            if is_dataclass(getattr(target, f.name)):
                continue
            value = getattr(target, f.name)
            if section == "paths" and f.name in _EXAMPLE_PATHS:
                lines.append('%s = %s    # write "" to derive it from transcoded'
                             % (f.name, _toml_value(_EXAMPLE_PATHS[f.name])))
                continue
            lines.append("%s = %s" % (f.name, _toml_value(value)))
        lines.append("")
        for f in fields(target):
            value = getattr(target, f.name)
            if not is_dataclass(value):
                continue
            lines.append("[%s.%s]" % (section, f.name))
            for inner in fields(value):
                lines.append("%s = %s" % (inner.name, _toml_value(getattr(value, inner.name))))
            lines.append("")
    return "\n".join(lines)


def _toml_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return "[" + ", ".join('"%s"' % item for item in value) + "]"
    if value is None:
        return '""'
    if isinstance(value, (int, float)):
        return str(value)
    return '"%s"' % value


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
