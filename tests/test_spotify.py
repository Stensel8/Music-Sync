from typing import Any

import pytest
import responses

from musicsync.errors import ProviderError
from musicsync.http import ApiClient
from musicsync.models import Track
from musicsync.providers.spotify import API, SpotifyProvider, parse_track

from .support import ISRC_A, ISRC_B, sent_json, sent_query


@pytest.fixture
def spotify() -> SpotifyProvider:
    return SpotifyProvider(ApiClient(API, lambda force: "tok", sleep=lambda _: None))


def sp_track(name: str = "Song", uri: str = "spotify:track:1", isrc: str = ISRC_A, **extra: Any) -> dict[str, Any]:
    """A track object as the Spotify API sends it."""
    return {
        "type": "track",
        "name": name,
        "uri": uri,
        "duration_ms": 200000,
        "artists": [{"name": "Artist"}],
        "album": {"name": "Album"},
        "external_ids": {"isrc": isrc},
        **extra,
    }


def test_parse_track_maps_the_fields():
    assert parse_track(sp_track()) == Track("Song", ["Artist"], "Album", 200000, ISRC_A, {"spotify": "spotify:track:1"})


@pytest.mark.parametrize("entry", [None, {}, {"type": "episode", "name": "A podcast"}, {"type": "track", "name": ""}])
def test_parse_track_skips_empty_entries_and_podcast_episodes(entry):
    assert parse_track(entry) is None


def test_local_files_have_no_usable_id():
    local = parse_track(sp_track(is_local=True))
    assert local is not None and local.ids == {}


@responses.activate
def test_liked_tracks_follow_the_pagination(spotify):
    responses.get(
        f"{API}/me/tracks", json={"items": [{"track": sp_track("One")}], "next": f"{API}/me/tracks?offset=1&limit=50"}
    )
    responses.get(
        f"{API}/me/tracks?offset=1&limit=50",
        json={"items": [{"track": sp_track("Two")}, {"track": None}], "next": None},
    )
    assert [t.title for t in spotify.liked_tracks()] == ["One", "Two"]
    assert sent_query(responses.calls[0])["limit"] == ["50"]


@responses.activate
def test_playlists_flag_the_ones_whose_contents_you_cannot_read(spotify):
    responses.get(f"{API}/me", json={"id": "me"})
    responses.get(
        f"{API}/me/playlists",
        json={
            "items": [
                {"id": "a", "name": "Mine", "owner": {"id": "me"}, "items": {"total": 5}},
                {"id": "b", "name": "Shared", "owner": {"id": "x"}, "collaborative": True, "tracks": {"total": 3}},
                {"id": "c", "name": "Followed", "owner": {"id": "x"}, "items": {"total": 9}},
                None,
            ],
        },
    )
    found = {p.name: (p.track_count, p.readable) for p in spotify.playlists()}
    assert found == {"Mine": (5, True), "Shared": (3, True), "Followed": (9, False)}
    assert len(responses.calls) == 2  # the user id is fetched once, not per playlist


@responses.activate
def test_playlist_tracks_read_both_the_new_and_the_old_field_name(spotify):
    responses.get(
        f"{API}/playlists/p1/items",
        json={"items": [{"item": sp_track("New style")}, {"track": sp_track("Old style")}, None]},
    )
    assert [t.title for t in spotify.playlist_tracks("p1")] == ["New style", "Old style"]


@responses.activate
def test_someone_elses_playlist_explains_the_restriction(spotify):
    responses.get(f"{API}/playlists/p1/items", status=403, json={"error": {"status": 403, "message": "Forbidden"}})
    with pytest.raises(ProviderError, match="own or collaborate"):
        list(spotify.playlist_tracks("p1"))


@responses.activate
def test_search_asks_for_ten_results_at_most(spotify):
    responses.get(f"{API}/search", json={"tracks": {"items": [sp_track(), {"type": "episode"}]}})
    assert [t.title for t in spotify.search("song artist")] == ["Song"]
    assert sent_query(responses.calls[0]) == {"q": ["song artist"], "type": ["track"], "limit": ["10"]}


@responses.activate
def test_isrc_lookup_is_one_search_per_code_and_leaves_out_what_is_not_found(spotify):
    responses.get(f"{API}/search", json={"tracks": {"items": [sp_track()]}})
    responses.get(f"{API}/search", json={"tracks": {"items": []}})
    found = spotify.lookup_isrcs([ISRC_A, ISRC_B])
    assert list(found) == [ISRC_A]
    assert [sent_query(call)["q"] for call in responses.calls] == [[f"isrc:{ISRC_A}"], [f"isrc:{ISRC_B}"]]


@responses.activate
def test_create_playlist_is_private_and_uses_me_playlists(spotify):
    responses.post(f"{API}/me/playlists", json={"id": "new"})
    assert spotify.create_playlist("Mix", "About") == "new"
    assert sent_json(responses.calls[0]) == {"name": "Mix", "description": "About", "public": False}


@responses.activate
def test_add_to_playlist_batches_by_100_and_skips_tracks_without_an_id(spotify):
    responses.post(f"{API}/playlists/p1/items", json={"snapshot_id": "s"})
    tracks = [Track(f"T{i}", ids={"spotify": f"spotify:track:{i}"}) for i in range(250)] + [Track("No id")]
    spotify.add_to_playlist("p1", tracks)
    assert [len(sent_json(call)["uris"]) for call in responses.calls] == [100, 100, 50]


@responses.activate
def test_resolve_prefers_the_isrc_and_falls_back_to_a_text_search(spotify):
    responses.get(f"{API}/search", json={"tracks": {"items": [sp_track("Other Song", "spotify:track:9")]}})
    by_isrc = spotify.resolve(Track("Song", ["Artist"], isrc=ISRC_A))
    assert by_isrc is not None and by_isrc.method == "isrc"  # the ISRC decides, whatever the title says

    responses.replace(responses.GET, f"{API}/search", json={"tracks": {"items": [sp_track("Song", "spotify:track:5")]}})
    by_text = spotify.resolve(Track("Song", ["Artist"], duration_ms=200000))
    assert by_text is not None and by_text.method == "search" and by_text.track.ids == {"spotify": "spotify:track:5"}


@responses.activate
def test_a_premium_refusal_is_explained_and_not_mistaken_for_someone_elses_playlist(spotify):
    error = {"error": {"status": 403, "message": "Active premium subscription required for the owner of the app"}}
    responses.get(f"{API}/me/tracks", status=403, json=error)
    responses.get(f"{API}/playlists/p1/items", status=403, json=error)
    with pytest.raises(ProviderError, match="Premium"):
        list(spotify.liked_tracks())
    with pytest.raises(ProviderError, match="Premium"):
        list(spotify.playlist_tracks("p1"))
