from typing import Any

import pytest
import responses

from musicsync.http import ApiClient
from musicsync.models import Track
from musicsync.providers.tidal import API, TidalProvider, parse_duration, parse_tracks

from .support import ISRC_A, ISRC_B, sent_json, sent_query


@pytest.fixture
def tidal() -> TidalProvider:
    return TidalProvider(ApiClient(API, lambda force: "tok", sleep=lambda _: None), country="NL")


def tidal_track(
    track_id: str, title: str = "Get Lucky", version: str | None = None, isrc: str = ISRC_A
) -> dict[str, Any]:
    """A track resource as the TIDAL API sends it: artist and album are references into ``included``."""
    attributes = {"title": title, "isrc": isrc, "duration": "PT4M8S", **({"version": version} if version else {})}
    return {
        "id": track_id,
        "type": "tracks",
        "attributes": attributes,
        "relationships": {
            "artists": {"data": [{"id": "9", "type": "artists"}]},
            "albums": {"data": [{"id": "5", "type": "albums"}]},
        },
    }


INCLUDED = [
    {"id": "9", "type": "artists", "attributes": {"name": "Daft Punk"}},
    {"id": "5", "type": "albums", "attributes": {"title": "Random Access Memories"}},
]
COLLECTION = f"{API}/userCollectionTracks/me/relationships/items"


@pytest.mark.parametrize(
    ("value", "milliseconds"),
    [("PT4M8S", 248000), ("PT1H2M3.5S", 3723500), ("PT30S", 30000), ("nonsense", None), ("PT", None), (None, None)],
)
def test_parse_duration(value, milliseconds):
    assert parse_duration(value) == milliseconds


def test_parse_tracks_resolves_artist_album_and_version():
    (parsed,) = parse_tracks({"data": [tidal_track("111", version="Radio Edit")], "included": INCLUDED})
    expected = Track(
        "Get Lucky (Radio Edit)", ["Daft Punk"], "Random Access Memories", 248000, ISRC_A, {"tidal": "111"}
    )
    assert parsed == expected


def test_parse_tracks_ignores_videos_and_untitled_entries():
    doc = {"data": [{"id": "1", "type": "videos", "attributes": {"title": "Clip"}}, {"id": "2", "type": "tracks"}]}
    assert parse_tracks(doc) == []


@responses.activate
def test_liked_tracks_list_ids_first_then_fetch_full_tracks_in_batches_of_twenty(tidal):
    ids = [str(n) for n in range(1, 26)]
    responses.get(COLLECTION, json={"data": [{"id": i, "type": "tracks"} for i in ids]})
    responses.get(f"{API}/tracks", json={"data": [tidal_track("1")], "included": INCLUDED})
    list(tidal.liked_tracks())
    batches = [sent_query(call)["filter[id]"] for call in responses.calls[1:]]
    assert [len(batch) for batch in batches] == [20, 5]  # one repeated filter[id] parameter per id, as the spec says
    assert sent_query(responses.calls[0])["countryCode"] == ["NL"]
    assert sent_query(responses.calls[1])["include"] == ["artists,albums"]


@responses.activate
def test_liked_tracks_keep_their_order_and_follow_the_cursor(tidal):
    next_page = "/userCollectionTracks/me/relationships/items?page%5Bcursor%5D=CUR"
    responses.get(
        COLLECTION,
        json={"data": [{"id": "2", "type": "tracks"}, {"id": "1", "type": "tracks"}], "links": {"next": next_page}},
    )
    responses.get(COLLECTION, json={"data": [{"id": "3", "type": "tracks"}]})
    responses.get(
        f"{API}/tracks",
        json={
            "data": [tidal_track("1", "One"), tidal_track("2", "Two"), tidal_track("3", "Three")],
            "included": INCLUDED,
        },
    )
    assert [t.title for t in tidal.liked_tracks()] == ["Two", "One", "Three"]
    pages = [sent_query(call) for call in responses.calls if "userCollectionTracks" in (call.request.url or "")]
    assert "page[cursor]" not in pages[0] and pages[1]["page[cursor]"] == ["CUR"]


@responses.activate
def test_a_cursor_that_never_ends_does_not_loop_forever(tidal):
    stuck = {"data": [], "links": {"next": "/x?page%5Bcursor%5D=SAME"}}
    responses.get(COLLECTION, json=stuck)
    responses.get(COLLECTION, json=stuck)
    responses.get(COLLECTION, json=stuck)
    assert list(tidal.liked_tracks()) == []
    assert len(responses.calls) == 2


@responses.activate
def test_playlists_are_the_ones_you_own(tidal):
    responses.get(
        f"{API}/playlists",
        json={
            "data": [{"id": "uuid-1", "type": "playlists", "attributes": {"name": "Road trip", "numberOfItems": 12}}]
        },
    )
    (playlist,) = tidal.playlists()
    assert (playlist.id, playlist.name, playlist.track_count, playlist.readable) == ("uuid-1", "Road trip", 12, True)
    assert sent_query(responses.calls[0])["filter[owners.id]"] == ["me"]


@responses.activate
def test_isrcs_are_looked_up_twenty_at_a_time_and_grouped_by_code(tidal):
    codes = [f"USAAA{n:07d}" for n in range(45)]
    responses.get(
        f"{API}/tracks",
        json={"data": [tidal_track("111", isrc=codes[0]), tidal_track("222", isrc=codes[1])], "included": INCLUDED},
    )
    found = tidal.lookup_isrcs(codes)
    assert [len(sent_query(call)["filter[isrc]"]) for call in responses.calls] == [20, 20, 5]
    assert sorted(found) == [codes[0], codes[1]] and found[codes[0]][0].ids == {"tidal": "111"}


@responses.activate
def test_search_uses_filter_query_and_keeps_tidals_relevance_order(tidal):
    relationships = {"tracks": {"data": [{"id": "2", "type": "tracks"}, {"id": "1", "type": "tracks"}]}}
    responses.get(
        f"{API}/searchResults", json={"data": [{"id": "s", "type": "searchResults", "relationships": relationships}]}
    )
    responses.get(
        f"{API}/tracks", json={"data": [tidal_track("1", "One"), tidal_track("2", "Two")], "included": INCLUDED}
    )
    assert [t.title for t in tidal.search("daft punk")] == ["Two", "One"]
    assert sent_query(responses.calls[0])["filter[query]"] == ["daft punk"]


@responses.activate
def test_create_playlist_posts_a_json_api_body(tidal):
    responses.post(f"{API}/playlists", json={"data": {"id": "uuid-9", "type": "playlists"}}, status=201)
    assert tidal.create_playlist("Mix", "About") == "uuid-9"
    request = responses.calls[0].request
    assert sent_json(responses.calls[0]) == {
        "data": {"type": "playlists", "attributes": {"name": "Mix", "description": "About"}}
    }
    assert request.headers["Content-Type"] == "application/vnd.api+json"


@responses.activate
def test_add_to_playlist_skips_duplicates_server_side_in_batches_of_twenty(tidal):
    responses.post(f"{API}/playlists/uuid-9/relationships/items", status=201)
    tracks = [Track(f"T{i}", ids={"tidal": str(i)}) for i in range(45)] + [Track("No id")]
    tidal.add_to_playlist("uuid-9", tracks)
    bodies = [sent_json(call) for call in responses.calls]
    assert [len(body["data"]) for body in bodies] == [20, 20, 5]
    assert all(body["meta"] == {"onDuplicates": "SKIP"} for body in bodies)
    assert bodies[0]["data"][0] == {"id": "0", "type": "tracks"}


@responses.activate
def test_an_isrc_lookup_reads_every_page(tidal):
    # One ISRC is often on a single, an album and a compilation: 20 codes can fill more than one page.
    next_page = "/tracks?page%5Bcursor%5D=P2"
    responses.get(
        f"{API}/tracks",
        json={"data": [tidal_track("111", isrc=ISRC_A)], "included": INCLUDED, "links": {"next": next_page}},
    )
    responses.get(f"{API}/tracks", json={"data": [tidal_track("222", isrc=ISRC_B)], "included": INCLUDED})
    found = tidal.lookup_isrcs([ISRC_A, ISRC_B])
    assert sorted(found) == [ISRC_A, ISRC_B]
    second = sent_query(responses.calls[1])
    assert second["page[cursor]"] == ["P2"] and second["filter[isrc]"] == [ISRC_A, ISRC_B]


@responses.activate
def test_find_finds_a_track_by_isrc_through_the_bulk_lookup(tidal):
    responses.get(f"{API}/tracks", json={"data": [tidal_track("111", isrc=ISRC_B)], "included": INCLUDED})
    match = tidal.find(Track("Whatever", ["Someone"], isrc=ISRC_B))
    assert match is not None and match.method == "isrc" and match.track.ids == {"tidal": "111"}
