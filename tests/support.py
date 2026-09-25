"""Test doubles and helpers shared by the test modules."""

import json
import time
from collections.abc import Collection, Iterable, Iterator
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from responses import Call

from musicsync.config import SERVICES, ServiceConfig, Settings
from musicsync.models import PlaylistInfo, Track
from musicsync.oauth import OAuthClient, Token, TokenStore
from musicsync.providers.base import Provider
from musicsync.services import Services

# Valid ISRCs: the format is checked when reading files, so tests need real-looking ones.
ISRC_A, ISRC_B, ISRC_C = "USAAA0000001", "USAAA0000002", "USAAA0000003"


def track(
    title: str,
    artist: str = "Artist",
    *,
    ids: dict[str, str] | None = None,
    isrc: str | None = None,
    duration_ms: int | None = 200_000,
    album: str = "",
) -> Track:
    return Track(title, [artist], album, duration_ms, isrc, ids or {})


class FakeProvider(Provider):
    """An in-memory service that knows the tracks in its ``catalog``."""

    def __init__(
        self,
        name: str = "fake",
        catalog: Iterable[Track] = (),
        liked: Iterable[Track] = (),
        *,
        readable: bool = True,
    ):
        self.name = name
        self.catalog = list(catalog)
        self.liked = list(liked)
        self.readable = readable  # whether its playlists count as the user's own
        self.playlists_by_id: dict[str, tuple[str, list[Track]]] = {}
        self.descriptions: dict[str, str] = {}  # of the playlists made through create_playlist
        self.isrc_lookups: list[list[str]] = []  # one entry per bulk lookup, to check the batching
        self.searches: list[str] = []  # every search asked
        self.add_calls = 0

    def liked_tracks(self) -> Iterator[Track]:
        yield from self.liked

    def playlists(self) -> list[PlaylistInfo]:
        return [
            PlaylistInfo(pid, name, len(tracks), self.readable) for pid, (name, tracks) in self.playlists_by_id.items()
        ]

    def playlist_tracks(self, playlist_id: str) -> Iterator[Track]:
        yield from self.playlists_by_id[playlist_id][1]

    def lookup_isrcs(self, isrcs: Collection[str]) -> dict[str, list[Track]]:
        self.isrc_lookups.append(sorted(isrcs))
        return {isrc: found for isrc in isrcs if (found := [t for t in self.catalog if t.isrc == isrc])}

    def search(self, query: str) -> list[Track]:
        self.searches.append(query)
        words = query.lower().split()
        return [t for t in self.catalog if all(word in f"{t.title} {' '.join(t.artists)}".lower() for word in words)]

    def create_playlist(self, name: str, description: str = "") -> str:
        playlist_id = f"pl{len(self.playlists_by_id) + 1}"
        self.playlists_by_id[playlist_id] = (name, [])
        self.descriptions[playlist_id] = description
        return playlist_id

    def add_to_playlist(self, playlist_id: str, tracks: list[Track]) -> None:
        self.add_calls += 1
        self.playlists_by_id[playlist_id][1].extend(tracks)

    def add_favorite_tracks(self, tracks: list[Track]) -> None:
        self.add_calls += 1
        self.liked.extend(tracks)

    def existing_playlist(self, name: str, *tracks: Track) -> str:
        """Test shortcut: a playlist that was already there, so it does not count as an add call."""
        playlist_id = self.create_playlist(name)
        self.playlists_by_id[playlist_id][1].extend(tracks)
        return playlist_id


class FakeOAuth(OAuthClient):
    """Logs in without a network: a fixed authorize URL and a fixed token."""

    name = "spotify"

    def __init__(self) -> None:
        super().__init__("client-id", "http://127.0.0.1:8888/callback")
        self.exchanged: tuple[str, str] | None = None

    def authorize_url(self, state: str, challenge: str) -> str:
        return f"https://auth.test/authorize?state={state}&code_challenge={challenge}"

    def exchange(self, code: str, verifier: str) -> Token:
        self.exchanged = (code, verifier)
        return Token("ACCESS", "REFRESH", time.time() + 3600)


class FakeServices(Services):
    """Services with fake providers and a fake login, and a token file inside ``tmp_path``."""

    def __init__(self, tmp_path: Path, settings: Settings | None = None, **providers: Provider):
        configured = Settings({name: ServiceConfig("client-id") for name in SERVICES})
        super().__init__(settings or configured, TokenStore(tmp_path / "tokens.json"))
        self.providers = providers
        self.fake_oauth = FakeOAuth()

    def provider(self, service: str) -> Provider:
        return self.providers[service]

    def oauth(self, service: str) -> OAuthClient:
        return self.fake_oauth


def _body(call: Call) -> str:
    body = call.request.body
    assert isinstance(body, str | bytes)
    return body.decode() if isinstance(body, bytes) else body


def sent_json(call: Call) -> dict[str, Any]:
    """The JSON body of a request captured by ``responses``."""
    return json.loads(_body(call))


def sent_form(call: Call) -> dict[str, list[str]]:
    """The form-encoded body of a request captured by ``responses``."""
    return parse_qs(_body(call))


def sent_query(call: Call) -> dict[str, list[str]]:
    """The query string of a request captured by ``responses``."""
    return parse_qs(urlsplit(call.request.url or "").query)
