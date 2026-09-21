"""Spotify Web API, following the rules for development-mode apps that apply since February 2026."""

from collections.abc import Collection, Iterator
from functools import cached_property
from itertools import batched
from typing import Any

from ..errors import ApiError, ProviderError
from ..http import ApiClient
from ..models import PlaylistInfo, Track, normalize_isrc
from ..oauth import OAuthClient
from .base import Provider

API = "https://api.spotify.com/v1"
PAGE_SIZE = 50
ADD_BATCH = 100  # tracks per "add to playlist" request


class SpotifyOAuth(OAuthClient):
    name = "spotify"
    authorize_endpoint = "https://accounts.spotify.com/authorize"
    token_endpoint = "https://accounts.spotify.com/api/token"
    scopes = (
        "user-library-read",
        "playlist-read-private",
        "playlist-read-collaborative",
        "playlist-modify-private",
        "playlist-modify-public",
    )


def parse_track(obj: dict[str, Any] | None) -> Track | None:
    """A Spotify track object as a Track; None for empty entries and podcast episodes."""
    if not obj or obj.get("type", "track") != "track" or not obj.get("name"):
        return None
    # Local files (uploaded by the user) have a URI that no one else can use.
    uri = None if obj.get("is_local") else obj.get("uri")
    return Track(
        title=obj["name"],
        artists=[artist["name"] for artist in obj.get("artists") or [] if artist.get("name")],
        album=(obj.get("album") or {}).get("name", ""),
        duration_ms=obj.get("duration_ms"),
        isrc=normalize_isrc((obj.get("external_ids") or {}).get("isrc")),
        ids={"spotify": uri} if uri else {},
    )


class SpotifyProvider(Provider):
    name = "spotify"

    def __init__(self, api: ApiClient):
        self.api = api

    @cached_property
    def _me(self) -> str:
        """The user's id: it tells your own playlists from the ones you merely follow."""
        return self.api.request("GET", "/me")["id"]

    def _pages(self, path: str) -> Iterator[dict[str, Any]]:
        """Every page of a list endpoint, following the "next" links Spotify sends."""
        page = self.api.request("GET", path, params={"limit": PAGE_SIZE})
        yield page
        while next_url := page.get("next"):
            page = self.api.request("GET", next_url)  # a complete URL, parameters included
            yield page

    def liked_tracks(self) -> Iterator[Track]:
        for page in self._pages("/me/tracks"):
            for entry in page.get("items", []):
                if track := parse_track(entry.get("track")):
                    yield track

    def playlists(self) -> list[PlaylistInfo]:
        found = []
        for page in self._pages("/me/playlists"):
            for playlist in filter(None, page.get("items", [])):
                # February 2026 renamed the "tracks" summary of a playlist to "items".
                summary = playlist.get("items") if isinstance(playlist.get("items"), dict) else playlist.get("tracks")
                owned = (playlist.get("owner") or {}).get("id") == self._me
                found.append(
                    PlaylistInfo(
                        id=playlist["id"],
                        name=playlist.get("name") or "",
                        track_count=(summary or {}).get("total"),
                        readable=owned or bool(playlist.get("collaborative")),
                    )
                )
        return found

    def playlist_tracks(self, playlist_id: str) -> Iterator[Track]:
        try:
            for page in self._pages(f"/playlists/{playlist_id}/items"):
                for entry in filter(None, page.get("items", [])):
                    # The track sits under "item" since February 2026 (it used to be "track").
                    if track := parse_track(entry.get("item") or entry.get("track")):
                        yield track
        except ApiError as exc:
            if exc.status in (403, 404):
                raise ProviderError(
                    f"Spotify only lets apps read the tracks of playlists you own or collaborate on ({playlist_id})."
                ) from exc
            raise

    def search(self, query: str) -> list[Track]:
        # Apps in development mode get at most 10 results per search request.
        data = self.api.request("GET", "/search", params={"q": query, "type": "track", "limit": 10})
        return [track for item in data.get("tracks", {}).get("items", []) if (track := parse_track(item))]

    def lookup_isrcs(self, isrcs: Collection[str]) -> dict[str, list[Track]]:
        # Spotify has no bulk ISRC lookup, so this is one search per code.
        return {isrc: found for isrc in isrcs if (found := self.search(f"isrc:{isrc}"))}

    def create_playlist(self, name: str, description: str = "") -> str:
        data = self.api.request(
            "POST", "/me/playlists", json={"name": name, "description": description, "public": False}
        )
        return data["id"]

    def add_to_playlist(self, playlist_id: str, tracks: list[Track]) -> None:
        uris = [uri for track in tracks if (uri := self.native_id(track))]
        for batch in batched(uris, ADD_BATCH, strict=False):
            self.api.request("POST", f"/playlists/{playlist_id}/items", json={"uris": list(batch)})
