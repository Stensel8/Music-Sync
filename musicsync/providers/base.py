"""What a music service has to offer, and the lookup logic all services share."""

from abc import ABC, abstractmethod
from collections.abc import Collection, Iterator, Mapping
from functools import partial

from ..errors import ApiError, ProviderError
from ..matching import best_match, rank, searchable, simplify_title
from ..models import Match, PlaylistInfo, Track

# HTTP statuses that mean "this one query or code cannot be looked up", as opposed to "the run is broken".
UNLOOKUPABLE = (400, 404)
# A search hit this good ends the search: the remaining queries are not going to find a better one.
CONVINCING = 0.95


def search_queries(track: Track) -> list[str]:
    """What to search for, most specific first: the title as written, without "(feat. ...)" and
    "- Remastered" noise, without accents and punctuation, and the title alone (for an artist that the
    other service spells differently). Queries that differ only in case or spacing are asked once."""
    simple = simplify_title(track.title)
    queries = [
        f"{track.title} {track.artist}",
        f"{simple} {track.artist}",
        f"{searchable(simple)} {searchable(track.artist)}",
        simple,
    ]
    unique: dict[str, str] = {}
    for query in queries:
        unique.setdefault(" ".join(query.casefold().split()), " ".join(query.split()))
    return [query for query in unique.values() if query]


class Provider(ABC):
    """One music service. ``name`` is also the key of its ids in ``Track.ids``."""

    name: str
    add_batch = 100  # the most tracks one "add to playlist" request takes
    favorite_batch = 20  # the most tracks one "add to favourites" request takes

    @property
    def label(self) -> str:
        """The name to show people: "Spotify", "Tidal"."""
        return self.name.title()

    @abstractmethod
    def liked_tracks(self) -> Iterator[Track]:
        """The user's liked / saved tracks."""

    def liked_count(self) -> int | None:
        """How many liked tracks there are, if the service says so without reading them all."""
        return None

    @abstractmethod
    def playlists(self) -> list[PlaylistInfo]:
        """The user's playlists."""

    @abstractmethod
    def playlist_tracks(self, playlist_id: str) -> Iterator[Track]:
        """The tracks of one playlist."""

    @abstractmethod
    def lookup_isrcs(self, isrcs: Collection[str]) -> dict[str, list[Track]]:
        """Find tracks by ISRC in bulk. Codes that match nothing are left out of the result."""

    @abstractmethod
    def search(self, query: str) -> list[Track]:
        """Free-text search, best result first."""

    @abstractmethod
    def search_albums(self, query: str) -> list[Track]:
        """Free-text search for albums, best first. An album comes as a Track with the album's title."""

    @abstractmethod
    def create_playlist(self, name: str, description: str = "") -> str:
        """Create a private playlist and return its id."""

    @abstractmethod
    def add_to_playlist(self, playlist_id: str, tracks: list[Track]) -> None:
        """Add tracks that have an id on this service, at most ``add_batch``, in one request."""

    @abstractmethod
    def add_favorite_tracks(self, tracks: list[Track]) -> None:
        """Add tracks that have an id on this service to the favourites (liked songs), at most ``favorite_batch``,
        in one request."""

    @abstractmethod
    def add_favorite_albums(self, albums: list[Track]) -> None:
        """Add albums that have an id on this service to the favourites, at most ``favorite_batch``, in one request."""

    def _refused(self, what: str) -> ProviderError:
        """A write that the service refused (HTTP 403): a login from before Music-Sync asked to change ``what``."""
        return ProviderError(
            f"{self.label} did not let Music-Sync change your {what}. Log in again (music-sync login {self.name}, "
            "or Log in on the web page) and allow it: older logins did not ask for that."
        )

    def native_id(self, track: Track) -> str | None:
        """This service's id for ``track``, if it has one."""
        return track.ids.get(self.name)

    def find_playlist(self, name_or_id: str) -> PlaylistInfo | None:
        """One of the user's playlists, by id or by name (ignoring case)."""
        playlists = self.playlists()
        by_id = next((p for p in playlists if p.id == name_or_id), None)
        return by_id or next((p for p in playlists if p.name.casefold() == name_or_id.casefold()), None)

    def find(self, track: Track, known_isrcs: Mapping[str, list[Track]] | None = None) -> Match | None:
        """The closest thing to ``track`` here: by id, then ISRC, then search. ``known_isrcs`` is a bulk lookup
        done by the caller. A search hit can score below what the caller accepts, so it can tell the user
        what came closest; None means nothing came close at all."""
        if self.native_id(track):
            return Match(track, 1.0, "id")
        if track.isrc:
            isrc = track.isrc.upper()
            found = known_isrcs.get(isrc, []) if known_isrcs is not None else self.lookup_isrcs([isrc]).get(isrc, [])
            if found:
                # An ISRC can appear on several releases of one recording; take the closest edition.
                return Match(max(found, key=partial(rank, track)), 1.0, "isrc")

        best: tuple[Track, float] | None = None
        for query in search_queries(track):
            try:
                results = self.search(query)
            except ApiError as exc:
                if exc.status not in UNLOOKUPABLE:
                    raise
                continue  # the service could not handle this query; the next one may do
            # The best of this search and the earlier ones; on a tie the earlier, more specific search wins.
            best = best_match(track, [best[0], *results] if best else results, min_score=0)
            if best and best[1] >= CONVINCING:
                break
        return Match(best[0], best[1], "search") if best and best[1] > 0 else None
