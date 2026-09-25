"""Tidal, through the official TIDAL API v2 (JSON:API: related resources come in ``included``, on request).

Catalogue lookups use an app-level token when a client secret is set; the user's own data uses the user's token.
"""

import re
from collections.abc import Collection, Iterator
from itertools import batched
from typing import Any
from urllib.parse import parse_qs, urlsplit

from ..http import ApiClient
from ..models import PlaylistInfo, Track, normalize_isrc
from ..oauth import OAuthClient
from .base import Provider

API = "https://openapi.tidal.com/v2"
JSONAPI = {"Accept": "application/vnd.api+json"}
JSONAPI_BODY = {**JSONAPI, "Content-Type": "application/vnd.api+json"}
BATCH = 20  # Tidal accepts at most 20 ids (or ISRCs) per filter, and 20 items per playlist request


class TidalOAuth(OAuthClient):
    name = "tidal"
    authorize_endpoint = "https://login.tidal.com/authorize"
    token_endpoint = "https://auth.tidal.com/v1/oauth2/token"
    scopes = ("collection.read", "playlists.read", "playlists.write")


_DURATION = re.compile(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?")


def parse_duration(value: str | None) -> int | None:
    """An ISO 8601 duration ("PT3M24S") in milliseconds."""
    match = _DURATION.fullmatch(value or "")
    if not match or not any(match.groups()):
        return None
    hours, minutes, seconds = (float(part or 0) for part in match.groups())
    return round((hours * 3600 + minutes * 60 + seconds) * 1000)


def parse_tracks(doc: dict[str, Any]) -> list[Track]:
    """The tracks in a JSON:API document, with the artist and album names found in ``included``."""
    included = {(res["type"], str(res["id"])): res.get("attributes", {}) for res in doc.get("included", [])}

    def related(track: dict[str, Any], kind: str, field: str) -> list[str]:
        refs = track.get("relationships", {}).get(kind, {}).get("data") or []
        return [name for ref in refs if (name := included.get((ref["type"], str(ref["id"])), {}).get(field))]

    tracks = []
    for res in doc.get("data", []):
        attrs = res.get("attributes", {})
        if res.get("type") != "tracks" or not attrs.get("title"):
            continue
        # Tidal keeps "Remastered 2011" and the like in a separate "version" field.
        title = f"{attrs['title']} ({attrs['version']})" if attrs.get("version") else attrs["title"]
        tracks.append(
            Track(
                title=title,
                artists=related(res, "artists", "name"),
                album=next(iter(related(res, "albums", "title")), ""),
                duration_ms=parse_duration(attrs.get("duration")),
                isrc=normalize_isrc(attrs.get("isrc")),
                ids={"tidal": str(res["id"])},
            )
        )
    return tracks


def _track_ids(doc: dict[str, Any]) -> list[str]:
    """The ids of the tracks in a relationship document (videos are skipped)."""
    return [str(ref["id"]) for ref in doc.get("data", []) if ref.get("type") == "tracks"]


def _next_cursor(doc: dict[str, Any]) -> str | None:
    """The cursor of the next page, from the ``links.next`` URL Tidal sends."""
    link = doc.get("links", {}).get("next")
    return parse_qs(urlsplit(link).query).get("page[cursor]", [None])[0] if link else None


class TidalProvider(Provider):
    name = "tidal"
    add_batch = BATCH

    def __init__(self, api: ApiClient, catalog: ApiClient | None = None, country: str = "US"):
        self.api = api  # the user's own token
        self.catalog = catalog or api  # app-level token when there is a secret, else the user's
        self.country = country  # Tidal wants a country code on nearly every request

    def _get(self, client: ApiClient, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return client.request("GET", path, params={"countryCode": self.country, **(params or {})}, headers=JSONAPI)

    def _pages(self, client: ApiClient, path: str, params: dict[str, Any] | None = None) -> Iterator[dict[str, Any]]:
        """Every page of a list endpoint. Tidal pages by opaque cursor, not by offset."""
        cursor = None
        while True:
            doc = self._get(client, path, {**(params or {}), **({"page[cursor]": cursor} if cursor else {})})
            yield doc
            following = _next_cursor(doc)
            if not following or following == cursor:  # the second check guards against a server that never ends
                return
            cursor = following

    def _tracks_by_id(self, ids: list[str]) -> list[Track]:
        """Full tracks (with artist and album names) for ``ids``, in the same order."""
        found: dict[str, Track] = {}
        for batch in batched(ids, BATCH, strict=False):
            doc = self._get(self.catalog, "/tracks", {"filter[id]": list(batch), "include": "artists,albums"})
            found.update({track.ids["tidal"]: track for track in parse_tracks(doc)})
        return [found[track_id] for track_id in ids if track_id in found]

    def liked_tracks(self) -> Iterator[Track]:
        # The collection only lists ids; the details come from a second, batched request per page.
        for page in self._pages(self.api, "/userCollectionTracks/me/relationships/items"):
            yield from self._tracks_by_id(_track_ids(page))

    def playlists(self) -> list[PlaylistInfo]:
        found = []
        for page in self._pages(self.api, "/playlists", {"filter[owners.id]": "me"}):
            for res in page.get("data", []):
                attrs = res.get("attributes", {})
                found.append(PlaylistInfo(str(res["id"]), attrs.get("name") or "", attrs.get("numberOfItems")))
        return found

    def playlist_tracks(self, playlist_id: str) -> Iterator[Track]:
        for page in self._pages(self.api, f"/playlists/{playlist_id}/relationships/items"):
            yield from self._tracks_by_id(_track_ids(page))

    def lookup_isrcs(self, isrcs: Collection[str]) -> dict[str, list[Track]]:
        found: dict[str, list[Track]] = {}
        for batch in batched(sorted(isrcs), BATCH, strict=False):  # 20 codes per request
            # One ISRC can be on several releases (single, album, compilation), so 20 codes can find more
            # tracks than fit on one page. Without the next pages those codes would seem unknown.
            params = {"filter[isrc]": list(batch), "include": "artists,albums"}
            for doc in self._pages(self.catalog, "/tracks", params):
                for track in parse_tracks(doc):
                    if track.isrc:
                        found.setdefault(track.isrc, []).append(track)
        return found

    def search(self, query: str) -> list[Track]:
        doc = self._get(self.catalog, "/searchResults", {"filter[query]": query[:100], "include": "tracks"})
        ids = [
            track_id
            for res in doc.get("data", [])
            for track_id in _track_ids(res.get("relationships", {}).get("tracks", {}))
        ]
        return self._tracks_by_id(ids[:BATCH])  # one request for all of them

    def create_playlist(self, name: str, description: str = "") -> str:
        # accessType is left out so the playlist gets Tidal's default (private) visibility.
        body = {"data": {"type": "playlists", "attributes": {"name": name, "description": description}}}
        doc = self.api.request(
            "POST", "/playlists", params={"countryCode": self.country}, json=body, headers=JSONAPI_BODY
        )
        return str(doc["data"]["id"])

    def add_to_playlist(self, playlist_id: str, tracks: list[Track]) -> None:
        body = {
            "data": [{"id": track_id, "type": "tracks"} for track in tracks if (track_id := self.native_id(track))],
            "meta": {"onDuplicates": "SKIP"},  # belt and braces: sync.py also skips what is already there
        }
        self.api.request(
            "POST",
            f"/playlists/{playlist_id}/relationships/items",
            params={"countryCode": self.country},
            json=body,
            headers=JSONAPI_BODY,
        )
