"""Bringing tracks to a service: find each one there, then add the matches to a playlist."""

from collections.abc import Callable
from dataclasses import dataclass, field
from itertools import batched

from .errors import ApiError, ProviderError
from .models import Match, Track
from .providers.base import Provider

LIKED = "Liked Songs"  # our name for a service's liked / saved tracks
CHUNK = 20  # tracks per bulk ISRC lookup: what Tidal takes in one request
# HTTP statuses that mean "this one track cannot be looked up", as opposed to "the run is broken".
UNLOOKUPABLE = (400, 404)

type Progress = Callable[[int, int, Track, Match | None], None]


@dataclass(slots=True)
class ImportResult:
    playlist_name: str
    playlist_id: str | None = None
    matched: list[Match] = field(default_factory=list)
    unmatched: list[Track] = field(default_factory=list)
    duplicates: int = 0  # repeated within the input
    already_there: int = 0  # already in the target playlist
    added: int = 0
    dry_run: bool = False


def select_tracks(
    provider: Provider,
    *,
    liked: bool = False,
    playlist: str | None = None,
    on_skip: Callable[[str], None] | None = None,
) -> list[tuple[str, list[Track]]]:
    """The (name, tracks) pairs to work on: liked songs, one playlist (by name or id), or, with
    neither asked for, the liked songs plus every playlist that can be read."""
    if liked:
        return [(LIKED, list(provider.liked_tracks()))]
    if playlist:
        info = provider.find_playlist(playlist)
        if info is None:
            raise ProviderError(f"No playlist with name or id {playlist!r} on {provider.name}.")
        if not info.readable:
            raise ProviderError(f'"{info.name}" is not yours to read (you neither own nor collaborate on it).')
        return [(info.name, list(provider.playlist_tracks(info.id)))]

    selected = [(LIKED, list(provider.liked_tracks()))]
    for info in provider.playlists():
        if info.readable:
            selected.append((info.name, list(provider.playlist_tracks(info.id))))
        elif on_skip:
            on_skip(f"skipping {info.name!r}: you neither own nor collaborate on it")
    return selected


def resolve_tracks(
    provider: Provider,
    tracks: list[Track],
    min_score: float = 0.8,
    progress: Progress | None = None,
) -> tuple[list[Match], list[Track]]:
    """Look every track up on ``provider``. Returns the matches and the tracks that were not found."""
    matches: list[Match] = []
    unmatched: list[Track] = []
    done = 0
    for chunk in batched(tracks, CHUNK, strict=False):
        # One request for the ISRCs of a whole chunk instead of one per track.
        isrcs = {track.isrc.upper() for track in chunk if track.isrc and not provider.native_id(track)}
        known = provider.lookup_isrcs(isrcs) if isrcs else {}
        for track in chunk:
            try:
                match = provider.resolve(track, min_score, known)
            except ApiError as exc:
                if exc.status not in UNLOOKUPABLE:
                    raise
                match = None
            if match:
                matches.append(match)
            else:
                unmatched.append(track)
            done += 1
            if progress:
                progress(done, len(tracks), track, match)
    return matches, unmatched


def import_tracks(
    provider: Provider,
    tracks: list[Track],
    playlist: str,
    *,
    min_score: float = 0.8,
    dry_run: bool = False,
    description: str = "Imported with Music-Sync",
    progress: Progress | None = None,
) -> ImportResult:
    """Add ``tracks`` to the playlist called ``playlist`` (created if missing), skipping what is already in it."""
    matches, unmatched = resolve_tracks(provider, tracks, min_score, progress)
    result = ImportResult(playlist_name=playlist, unmatched=unmatched, dry_run=dry_run)

    # Different source tracks can resolve to the same track on the target; keep the first of each.
    unique: dict[str, Match] = {}
    for match in matches:
        if native := provider.native_id(match.track):
            unique.setdefault(native, match)
    result.matched = list(unique.values())
    result.duplicates = len(matches) - len(unique)

    existing = provider.find_playlist(playlist)
    if existing and not existing.readable:
        raise ProviderError(f'"{existing.name}" is not yours to change (you neither own nor collaborate on it).')
    result.playlist_id = existing.id if existing else None
    present = (
        {n for track in provider.playlist_tracks(existing.id) if (n := provider.native_id(track))}
        if existing
        else set()
    )

    new = [match.track for native, match in unique.items() if native not in present]
    result.already_there = len(unique) - len(new)
    result.added = len(new)
    if new and not dry_run:
        result.playlist_id = result.playlist_id or provider.create_playlist(playlist, description)
        provider.add_to_playlist(result.playlist_id, new)
    return result
