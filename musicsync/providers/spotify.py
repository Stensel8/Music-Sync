"""Spotify Web API, following the rules for development-mode apps that apply since February 2026."""

from collections.abc import Collection, Iterator
from functools import cached_property
from typing import Any

from ..errors import ApiError, ProviderError
from ..http import ApiClient
from ..models import PlaylistInfo, Track, normalize_isrc
from ..oauth import OAuthClient
from .base import Provider

API = "https://api.spotify.com/v1"
PAGE_SIZE = 50

# Since February 2026 Spotify refuses apps whose owner has no Premium subscription, with a plain 403.
PREMIUM_REQUIRED = (
    "Spotify only lets apps whose owner has a Premium subscription use its API (a rule since February 2026). "
    'See "Spotify\'s API needs Premium" in the README for what you can do.'
)


class SpotifyOAuth(OAuthClient):
    name = "spotify"
    authorize_endpoint = "https://accounts.spotify.com/authorize"
    token_endpoint = "https://accounts.spotify.com/api/token"
    scopes = (
        "user-library-read",
        "user-library-modify",
        "playlist-read-private",
        "playlist-read-collaborative",
        "playlist-modify-private",
        "playlist-modify-public",
    )


def parse_track(obj: dict[str, Any] | None) -> Track | None:
    """A Spotify track (or album) object as a Track; None for empty entries and podcast episodes."""
    if not obj or obj.get("type", "track") not in ("track", "album") or not obj.get("name"):
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
    favorite_batch = 40  # what PUT /me/library takes

    def __init__(self, api: ApiClient):
        self.api = api

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        """``ApiClient.request``, but it explains the Premium rule when that is what refused us."""
        try:
            return self.api.request(method, path, **kwargs)
        except ApiError as exc:
            if exc.status == 403 and "premium" in exc.message.lower():
                raise ProviderError(PREMIUM_REQUIRED) from exc
            raise

    @cached_property
    def _me(self) -> str:
        """The user's id: it tells your own playlists from the ones you merely follow."""
        return self._request("GET", "/me")["id"]

    def _pages(self, path: str) -> Iterator[dict[str, Any]]:
        """Every page of a list endpoint, following the "next" links Spotify sends."""
        page = self._request("GET", path, params={"limit": PAGE_SIZE})
        yield page
        while next_url := page.get("next"):
            page = self._request("GET", next_url)  # a complete URL, parameters included
            yield page

    def liked_tracks(self) -> Iterator[Track]:
        for page in self._pages("/me/tracks"):
            for entry in page.get("items", []):
                if track := parse_track(entry.get("track")):
                    yield track

    def liked_count(self) -> int | None:
        return self._request("GET", "/me/tracks", params={"limit": 1}).get("total")

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

    def search(self, query: str, kind: str = "track") -> list[Track]:
        # Apps in development mode get at most 10 results per search request.
        data = self._request("GET", "/search", params={"q": query, "type": kind, "limit": 10})
        return [track for item in data.get(f"{kind}s", {}).get("items", []) if (track := parse_track(item))]

    def search_albums(self, query: str) -> list[Track]:
        return self.search(query, "album")

    def album_tracks(self, album_id: str) -> list[Track]:
        pages = self._pages(f"/albums/{album_id.rsplit(':', 1)[-1]}/tracks")  # the id from a URI
        return [track for page in pages for item in page.get("items", []) if (track := parse_track(item))]

    def lookup_isrcs(self, isrcs: Collection[str]) -> dict[str, list[Track]]:
        # Spotify has no bulk ISRC lookup, so this is one search per code.
        return {isrc: found for isrc in isrcs if (found := self.search(f"isrc:{isrc}"))}

    def create_playlist(self, name: str, description: str = "") -> str:
        data = self._request("POST", "/me/playlists", json={"name": name, "description": description, "public": False})
        return data["id"]

    def add_to_playlist(self, playlist_id: str, tracks: list[Track]) -> None:
        uris = [uri for track in tracks if (uri := self.native_id(track))]
        self._request("POST", f"/playlists/{playlist_id}/items", json={"uris": uris})

    def add_favorite_tracks(self, tracks: list[Track]) -> None:
        uris = [uri for track in tracks if (uri := self.native_id(track))]
        try:
            # Since February 2026 one endpoint saves every kind of item, by URI (spotipy does the same).
            self._request("PUT", "/me/library", params={"uris": ",".join(uris)})
        except ApiError as exc:
            if exc.status == 403:
                raise self._refused("library") from exc
            raise

    def add_favorite_albums(self, albums: list[Track]) -> None:
        self.add_favorite_tracks(albums)  # albums are saved the same way, by URI
