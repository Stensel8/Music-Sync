"""The ``music-sync`` command."""

import argparse
import logging
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from . import __version__
from .config import SERVICES
from .csvio import read_tracks, slug, write_tracks
from .errors import ConfigError, MusicSyncError, ProviderError
from .models import Match, Track
from .providers.base import Provider
from .services import Services
from .sync import LIKED, ImportResult, Progress, import_tracks, select_tracks

type Handler = Callable[[argparse.Namespace, Services], int]


def _warn(message: str) -> None:
    print(message, file=sys.stderr)


def _progress(quiet: bool) -> Progress | None:
    """A progress display on stderr: a live line on a terminal, a line per 50 tracks in a log."""
    if quiet:
        return None
    tty = sys.stderr.isatty()

    def show(done: int, total: int, track: Track, _match: Match | None) -> None:
        if tty:
            end = "\n" if done == total else ""
            print(f"\r[{done}/{total}] {str(track)[:60]:<60}", end=end, file=sys.stderr, flush=True)
        elif done % 50 == 0 or done == total:
            print(f"[{done}/{total}]", file=sys.stderr, flush=True)

    return show


def _print_result(result: ImportResult) -> None:
    verb = "would add" if result.dry_run else "added"
    counts = [(result.already_there, "already there"), (result.duplicates, "repeated in the input")]
    parts = [f"{verb} {result.added}", *(f"{n} {what}" for n, what in counts if n)]
    found = f"{len(result.matched)} matched, {len(result.unmatched)} not found"
    print(f"{result.playlist_name}: {found}; {', '.join(parts)}")


def _report_unmatched(unmatched: list[Track], path: str | None) -> None:
    """Show the tracks that were not found, or save them all when ``--unmatched`` was given."""
    if not unmatched:
        return
    if path:
        write_tracks(path, unmatched)
        print(f"{len(unmatched)} tracks without a match written to {path}")
        return
    print("\nNot found:")
    for track in unmatched[:10]:
        print(f"  {track}")
    if len(unmatched) > 10:
        print(f"  ... and {len(unmatched) - 10} more (use --unmatched FILE to save them all)")


# --- commands ---------------------------------------------------------------------------------------


def cmd_login(args: argparse.Namespace, services: Services) -> int:
    services.oauth(args.service).login(services.store)
    print(f"Logged in to {args.service}.")
    return 0


def cmd_logout(args: argparse.Namespace, services: Services) -> int:
    removed = services.store.delete(args.service)
    print(f"Logged out of {args.service}." if removed else f"Not logged in to {args.service}.")
    return 0


def cmd_status(_args: argparse.Namespace, services: Services) -> int:
    for service in SERVICES:
        token = services.store.load(service)
        if token is None:
            login = "not logged in"
        elif token.expired() and not token.refresh_token:
            login = "session expired"
        else:
            login = "logged in"
        client_id = "set" if services.settings.is_configured(service) else "MISSING"
        print(f"{service:8} client ID: {client_id:8} {login}")
    print(f"tokens are kept in {services.store.path}")
    return 0


def cmd_playlists(args: argparse.Namespace, services: Services) -> int:
    for info in services.provider(args.service).playlists():
        count = "?" if info.track_count is None else info.track_count
        note = "" if info.readable else "  (not yours: contents cannot be read)"
        print(f"{info.id}  {count:>5}  {info.name}{note}")
    return 0


def cmd_export(args: argparse.Namespace, services: Services) -> int:
    provider = services.provider(args.service)
    selected = select_tracks(provider, liked=args.liked, playlist=args.playlist, on_skip=_warn)
    if args.all:  # one CSV per playlist, in a folder
        folder = Path(args.output or f"music-sync-export/{args.service}")
        for name, tracks in selected:
            path = folder / f"{slug(name)}.csv"
            print(f"{write_tracks(path, tracks):>5} tracks -> {path}")
    else:
        ((name, tracks),) = selected
        path = Path(args.output or f"{slug(name)}.csv")
        print(f"{write_tracks(path, tracks)} tracks -> {path}")
    return 0


def cmd_import(args: argparse.Namespace, services: Services) -> int:
    tracks = read_tracks(args.file)
    if not tracks:
        print(f"No tracks found in {args.file}.")
        return 1
    result = import_tracks(
        services.provider(args.service),
        tracks,
        args.playlist,
        min_score=args.min_score,
        dry_run=args.dry_run,
        progress=_progress(args.quiet),
    )
    _print_result(result)
    _report_unmatched(result.unmatched, args.unmatched)
    return 0


def cmd_transfer(args: argparse.Namespace, services: Services) -> int:
    if args.source == args.target:
        raise ProviderError("Source and target are the same service.")
    if args.to_playlist and args.all:
        raise ProviderError("--to-playlist cannot be combined with --all.")
    source, target = services.provider(args.source), services.provider(args.target)
    unmatched: list[Track] = []
    for name, tracks in select_tracks(source, liked=args.liked, playlist=args.playlist, on_skip=_warn):
        if not tracks:
            continue
        destination = args.to_playlist or (f"{LIKED} (from {args.source.title()})" if name == LIKED else name)
        result = import_tracks(
            target,
            tracks,
            destination,
            min_score=args.min_score,
            dry_run=args.dry_run,
            progress=_progress(args.quiet),
        )
        _print_result(result)
        unmatched.extend(result.unmatched)
    _report_unmatched(unmatched, args.unmatched)
    return 0


def cmd_web(args: argparse.Namespace, services: Services) -> int:
    try:
        from .web import create_app  # Flask is an optional dependency
    except ImportError as exc:
        raise ConfigError("The web interface needs Flask: pip install 'music-sync[web]'") from exc
    print(f"Music-Sync web interface: http://127.0.0.1:{args.port}  (Ctrl+C to stop)")
    create_app(services).run(host="127.0.0.1", port=args.port, debug=False)
    return 0


def _probes(provider: Provider) -> list[tuple[str, Callable[[], str]]]:
    """The live checks ``doctor`` runs against one service, in order."""
    first_liked: list[Track] = []  # filled by the first probe, used by the ISRC probe

    def liked() -> str:
        if track := next(iter(provider.liked_tracks()), None):
            first_liked.append(track)
            return f"first liked track: {track}"
        return "library is empty"

    def playlists() -> str:
        return f"{len(provider.playlists())} playlists"

    def search() -> str:
        return f"{len(provider.search('Daft Punk Get Lucky'))} results"

    def isrc() -> str:
        code = first_liked[0].isrc if first_liked else None
        if not code:
            return "skipped (no liked track with an ISRC to test with)"
        if not provider.lookup_isrcs([code]).get(code):
            raise ProviderError(f"lookup of {code} found nothing")
        return f"{code} found"

    return [
        ("read liked songs", liked),
        ("list playlists", playlists),
        ("search the catalogue", search),
        ("look up by ISRC", isrc),
    ]


def cmd_doctor(args: argparse.Namespace, services: Services) -> int:
    """A live smoke test of the real APIs. Run it after setting up your credentials."""
    healthy = True
    for service in [args.service] if args.service else SERVICES:
        print(service)
        try:
            probes = _probes(services.provider(service))  # fails here when the credentials are missing
        except MusicSyncError as exc:
            print(f"  FAIL  configuration: {exc}")
            healthy = False
            continue
        print("  ok    configuration")
        for label, probe in probes:
            try:
                print(f"  ok    {label}: {probe()}")
            except MusicSyncError as exc:
                print(f"  FAIL  {label}: {exc}")
                healthy = False
    return 0 if healthy else 1


# --- argument parsing ---------------------------------------------------------------------------------


def _add_selection(parser: argparse.ArgumentParser) -> None:
    """--liked, --playlist or --all: which tracks to work on."""
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--liked", action="store_true", help="your liked / saved songs")
    group.add_argument("--playlist", metavar="NAME_OR_ID", help="one playlist, by name or id")
    group.add_argument("--all", action="store_true", help="liked songs and every playlist you can read")


def _add_matching_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--dry-run", action="store_true", help="look everything up, but change nothing")
    parser.add_argument(
        "--min-score", type=float, default=0.8, metavar="0-1", help="how sure a text match must be (default 0.8)"
    )
    parser.add_argument("--unmatched", metavar="FILE", help="write the tracks that were not found to this CSV")
    parser.add_argument("-q", "--quiet", action="store_true", help="no progress output")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="music-sync",
        description="Move playlists and liked songs between Spotify, Tidal and CSV.",
        suggest_on_error=True,  # Python 3.14: "invalid choice: 'tranfser', maybe you meant 'transfer'?"
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true", help="log every API call")
    commands = parser.add_subparsers(dest="command", required=True, metavar="command")

    def add(name: str, handler: Handler, help: str, *, service: bool = True) -> argparse.ArgumentParser:
        sub = commands.add_parser(name, help=help, description=help)
        sub.set_defaults(handler=handler)
        if service:
            sub.add_argument("service", choices=SERVICES, metavar="{spotify,tidal}")
        return sub

    add("login", cmd_login, "log in to a service in your browser")
    add("logout", cmd_logout, "forget the saved login of a service")
    add("status", cmd_status, "show what is configured and logged in", service=False)
    add("playlists", cmd_playlists, "list your playlists")

    export = add("export", cmd_export, "save liked songs or playlists as CSV")
    _add_selection(export)
    export.add_argument("-o", "--output", metavar="PATH", help="CSV file (a folder with --all)")

    imp = add("import", cmd_import, "add the tracks of a CSV file to a playlist")
    imp.add_argument("file", help="CSV file (see the README for the format)")
    imp.add_argument("--playlist", default="Music-Sync import", help="playlist to add to, created if missing")
    _add_matching_options(imp)

    transfer = add("transfer", cmd_transfer, "copy liked songs or playlists from one service to another", service=False)
    transfer.add_argument("source", choices=SERVICES, metavar="SOURCE")
    transfer.add_argument("target", choices=SERVICES, metavar="TARGET")
    _add_selection(transfer)
    transfer.add_argument("--to-playlist", metavar="NAME", help="playlist to fill (default: same name as the source)")
    _add_matching_options(transfer)

    web = add("web", cmd_web, "start the local web interface", service=False)
    web.add_argument("--port", type=int, default=8888, help="port to listen on (default 8888)")

    doctor = add("doctor", cmd_doctor, "test your setup against the real APIs", service=False)
    doctor.add_argument("service", nargs="?", choices=SERVICES, metavar="{spotify,tidal}")
    return parser


def main(argv: Sequence[str] | None = None, services: Services | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING, format="%(levelname)s %(name)s: %(message)s"
    )
    try:
        return args.handler(args, services or Services())
    except (MusicSyncError, OSError) as exc:  # OSError: a CSV file that is missing or cannot be written
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
