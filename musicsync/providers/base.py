"""What a music service has to offer, and the lookup logic all services share."""

from abc import ABC, abstractmethod
from collections.abc import Collection, Iterator, Mapping

from ..matching import best_match, score, simplify_title
from ..models import Match, PlaylistInfo, Track


class Provider(ABC):
    """One music service. ``name`` is also the key of its ids in ``Track.ids``."""

    name: str

    @abstractmethod
    def liked_tracks(self) -> Iterator[Track]:
        """The user's liked / saved tracks."""

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
    def create_playlist(self, name: str, description: str = "") -> str:
        """Create a private playlist and return its id."""

    @abstractmethod
    def add_to_playlist(self, playlist_id: str, tracks: list[Track]) -> None:
        """Add tracks that have an id on this service."""

    def native_id(self, track: Track) -> str | None:
        """This service's id for ``track``, if it has one."""
        return track.ids.get(self.name)

    def find_playlist(self, name_or_id: str) -> PlaylistInfo | None:
        """One of the user's playlists, by id or by name (ignoring case)."""
        playlists = self.playlists()
        by_id = next((p for p in playlists if p.id == name_or_id), None)
        return by_id or next((p for p in playlists if p.name.casefold() == name_or_id.casefold()), None)

    def resolve(
        self,
        track: Track,
        min_score: float = 0.8,
        known_isrcs: Mapping[str, list[Track]] | None = None,
    ) -> Match | None:
        """Find ``track`` here by id, then ISRC, then search. ``known_isrcs`` is a bulk lookup done by the caller."""
        if self.native_id(track):
            return Match(track, 1.0, "id")
        if track.isrc:
            isrc = track.isrc.upper()
            found = known_isrcs.get(isrc, []) if known_isrcs is not None else self.lookup_isrcs([isrc]).get(isrc, [])
            if found:
                # An ISRC can appear on several releases of one recording; take the closest edition.
                return Match(max(found, key=lambda candidate: score(track, candidate)), 1.0, "isrc")

        # Search on title and artist, as written and then without "(feat. ...)" and "- Remastered" noise.
        queries = dict.fromkeys([f"{track.title} {track.artist}", f"{simplify_title(track.title)} {track.artist}"])
        for query in queries:
            if hit := best_match(track, self.search(query.strip()), min_score):
                return Match(hit[0], hit[1], "search")
        return None
