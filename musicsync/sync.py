"""Bringing tracks to a service: find each one there, then add the matches to a playlist."""

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from itertools import batched
from typing import Literal

from .errors import ProviderError
from .matching import best_match, rejection, song_key
from .models import Match, PlaylistInfo, Track
from .providers.base import Provider

LIKED = "Liked Songs"  # our name for a service's liked / saved tracks
DESCRIPTION = "Imported with Music-Sync: https://github.com/Stensel8/Music-Sync"  # of the playlists it creates
CHUNK = 20  # tracks per bulk ISRC lookup: what Tidal takes in one request

# Reading the source, checking what the target playlist already has, finding the other tracks there, adding.
type Phase = Literal["read", "match", "check", "add"]


@dataclass(frozen=True, slots=True)
class Miss:
    """A track that was not found, why, and what came closest when that is worth showing."""

    track: Track
    reason: str  # for people: "score too low for a match (0.75, needs 0.80)"
    closest: Match | None = None

    @property
    def why(self) -> str:
        """The reason, with what came closest: "only another version; closest: Artist - Song (Live)"."""
        return f"{self.reason}; closest: {self.closest.track}" if self.closest else self.reason


@dataclass(frozen=True, slots=True)
class Step:
    """A progress report: ``done`` of ``total`` tracks through one step of the work. ``total`` is None
    while nobody knows it yet (Tidal does not say how many liked songs you have)."""

    phase: Phase
    text: str  # what is happening, for people: "Reading Road trip from Spotify"
    done: int
    total: int | None
    track: Track | None = None  # "match": the track just looked up
    found: int = 0  # "match": how many of the ``done`` tracks were found
    miss: Miss | None = None  # "match": why the track just looked up was not found


type Progress = Callable[[Step], None]


def remaining(done: int, total: int | None, elapsed: float) -> float | None:
    """Seconds still to go at the pace so far, once there is a pace to go by."""
    if total is None or done < 3 or elapsed < 2 or done >= total:
        return None
    return elapsed / done * (total - done)


@dataclass(slots=True)
class ImportResult:
    playlist_name: str
    playlist_id: str | None = None
    matched: list[Match] = field(default_factory=list)
    misses: list[Miss] = field(default_factory=list)  # the tracks that were not found
    duplicates: int = 0  # repeated within the input
    already_there: int = 0  # already in the target playlist
    added: int = 0
    dry_run: bool = False

    def summary(self) -> str:
        """One line: what was found, and what was (or would be) added."""
        verb = "would add" if self.dry_run else "added"
        counts = [(self.already_there, "already there"), (self.duplicates, "repeated in the input")]
        parts = [f"{verb} {self.added}", *(f"{n} {what}" for n, what in counts if n)]
        return f"{self.playlist_name}: {len(self.matched)} matched, {len(self.misses)} not found; {', '.join(parts)}"


def _read(
    tracks: Iterable[Track], text: str, total: int | None, progress: Progress | None, phase: Phase = "read"
) -> list[Track]:
    """All of ``tracks``, reporting each one read. ``total`` is what the service said to expect; the last
    report gives the real number, which differs when that was not known or out of date."""
    found: list[Track] = []
    for track in tracks:
        found.append(track)
        if progress:
            progress(Step(phase, text, len(found), None if total is None else max(total, len(found))))
    if progress and total != len(found):
        progress(Step(phase, text, len(found), len(found)))
    return found


def select_tracks(
    provider: Provider,
    *,
    liked: bool = False,
    playlist: str | None = None,
    on_skip: Callable[[str], None] | None = None,
    progress: Progress | None = None,
) -> list[tuple[str, list[Track]]]:
    """The (name, tracks) pairs to work on: liked songs, one playlist (by name or id), or, with
    neither asked for, the liked songs plus every playlist that can be read.
    Empty ones are left out: asking for one is an error, and among the rest they are reported to ``on_skip``."""

    def read_liked() -> tuple[str, list[Track]]:
        total = provider.liked_count() if progress else None  # it can cost a request: only when it is shown
        return LIKED, _read(provider.liked_tracks(), f"Reading {LIKED} from {provider.label}", total, progress)

    def read(info: PlaylistInfo) -> tuple[str, list[Track]]:
        text = f"Reading {info.name} from {provider.label}"
        return info.name, _read(provider.playlist_tracks(info.id), text, info.track_count, progress)

    if liked:
        found = [read_liked()]
    elif playlist:
        info = provider.find_playlist(playlist)
        if info is None:
            raise ProviderError(f"No playlist with name or id {playlist!r} on {provider.name}.")
        if not info.readable:
            raise ProviderError(f'"{info.name}" is not yours to read (you neither own nor collaborate on it).')
        found = [read(info)]
    else:
        found = [read_liked()]
        for info in provider.playlists():
            if info.readable:
                found.append(read(info))
            elif on_skip:
                on_skip(f"skipping {info.name!r}: you neither own nor collaborate on it")

    empty = [name for name, tracks in found if not tracks]
    if empty and (liked or playlist):
        raise ProviderError(f'"{empty[0]}" is empty.')
    for name in empty:
        if on_skip:
            on_skip(f"skipping {name!r}: it is empty")
    return [(name, tracks) for name, tracks in found if tracks]


def _miss(track: Track, closest: Match | None, provider: Provider, min_score: float) -> Miss:
    """Why ``track`` was not found, given the candidate that came closest (if any)."""
    if closest is None:
        return Miss(track, f"not on {provider.label}")
    match rejection(track, closest.track):
        case "other song":  # only the artist is the same: that candidate says nothing
            return Miss(track, f"only other songs on {provider.label}")
        case "other version":
            return Miss(track, "only another version", closest)
        case _:
            return Miss(track, f"score too low for a match ({closest.score:.2f}, needs {min_score:.2f})", closest)


class _Playlist:
    """The tracks already in the target playlist, by id, ISRC and song, so that a source track that is
    there needs no lookup. On a second run that is nearly every track (an idea from spotify_to_tidal)."""

    def __init__(self, provider: Provider, tracks: Iterable[Track]):
        self.provider = provider
        self.by_id: dict[str, Track] = {}
        self.by_isrc: dict[str, Track] = {}
        self.by_song: dict[tuple[str, str], list[Track]] = {}
        for track in tracks:
            if native := provider.native_id(track):
                self.by_id.setdefault(native, track)
            if track.isrc:
                self.by_isrc.setdefault(track.isrc.upper(), track)
            self.by_song.setdefault(song_key(track), []).append(track)

    def find(self, track: Track, min_score: float) -> Match | None:
        if (native := self.provider.native_id(track)) and native in self.by_id:
            return Match(self.by_id[native], 1.0, "playlist")
        if track.isrc and (there := self.by_isrc.get(track.isrc.upper())):
            return Match(there, 1.0, "playlist")
        hit = best_match(track, self.by_song.get(song_key(track), []), min_score)
        return Match(hit[0], hit[1], "playlist") if hit else None


def resolve_tracks(
    provider: Provider,
    tracks: list[Track],
    min_score: float = 0.8,
    progress: Progress | None = None,
    present: Iterable[Track] = (),
) -> tuple[list[Match], list[Miss]]:
    """Look every track up on ``provider``, except the ones in ``present`` (the target playlist), which are
    matched to those. Returns the matches, and the tracks that were not found with why."""
    playlist = _Playlist(provider, present)
    matches: list[Match] = []
    misses: list[Miss] = []
    text = f"Finding the tracks on {provider.label}"
    for chunk in batched(tracks, CHUNK, strict=False):
        there = [playlist.find(track, min_score) for track in chunk]
        # One request for the ISRCs of a whole chunk instead of one per track, for the tracks still to find.
        isrcs = {
            track.isrc.upper()
            for track, match in zip(chunk, there, strict=True)
            if track.isrc and not match and not provider.native_id(track)
        }
        known = provider.lookup_isrcs(isrcs) if isrcs else {}
        for track, match in zip(chunk, there, strict=True):
            miss = None
            if match is None:  # not in the playlist yet: look it up (find handles a search the service refuses)
                found = provider.find(track, known)
                match = found if found and found.score >= min_score else None
                miss = None if match else _miss(track, found, provider, min_score)
            if match:
                matches.append(match)
            if miss:
                misses.append(miss)
            if progress:
                progress(Step("match", text, len(matches) + len(misses), len(tracks), track, len(matches), miss))
    return matches, misses


def import_tracks(
    provider: Provider,
    tracks: list[Track],
    playlist: str,
    *,
    min_score: float = 0.8,
    dry_run: bool = False,
    description: str = DESCRIPTION,
    progress: Progress | None = None,
) -> ImportResult:
    """Add ``tracks`` to the playlist called ``playlist`` (created if missing), skipping what is already in it."""
    existing = provider.find_playlist(playlist)
    if existing and not existing.readable:
        raise ProviderError(f'"{existing.name}" is not yours to change (you neither own nor collaborate on it).')
    in_playlist: list[Track] = []
    if existing:
        text = f"Checking what is already in {existing.name} on {provider.label}"
        in_playlist = _read(provider.playlist_tracks(existing.id), text, existing.track_count, progress, "check")

    matches, misses = resolve_tracks(provider, tracks, min_score, progress, in_playlist)
    result = ImportResult(playlist, existing.id if existing else None, misses=misses, dry_run=dry_run)

    # Different source tracks can resolve to the same track on the target; keep the first of each.
    unique: dict[str, Match] = {}
    for match in matches:
        if native := provider.native_id(match.track):
            unique.setdefault(native, match)
    result.matched = list(unique.values())
    result.duplicates = len(matches) - len(unique)

    present = {native for track in in_playlist if (native := provider.native_id(track))}
    new = [match.track for native, match in unique.items() if native not in present]
    result.already_there = len(unique) - len(new)
    result.added = len(new)
    if new and not dry_run:
        text = f"Adding to {playlist} on {provider.label}"
        if progress:
            progress(Step("add", text, 0, len(new)))
        result.playlist_id = result.playlist_id or provider.create_playlist(playlist, description)
        added = 0
        for batch in batched(new, provider.add_batch, strict=False):  # one request each
            provider.add_to_playlist(result.playlist_id, list(batch))
            added += len(batch)
            if progress:
                progress(Step("add", text, added, len(new)))
    return result
