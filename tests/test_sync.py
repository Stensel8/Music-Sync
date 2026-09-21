import pytest

from musicsync.errors import ApiError, ProviderError, QuotaExceeded
from musicsync.models import Track
from musicsync.sync import import_tracks, resolve_tracks, select_tracks

from .support import ISRC_A, FakeProvider, track

CATALOG = [
    track("Song A", "Artist A", ids={"fake": "1"}, isrc=ISRC_A),
    track("Song B", "Artist B", ids={"fake": "2"}, duration_ms=180_000),
]


def source_tracks() -> list[Track]:
    return [
        Track("Song A", ["Artist A"], isrc=ISRC_A),  # found by ISRC
        Track("Song B (feat. X)", ["Artist B"], duration_ms=181_000),  # found by a text search
        Track("Nonexistent", ["Nobody"]),  # not found
        Track("Song A", ["Artist A"], isrc=ISRC_A),  # repeated
    ]


def ids_in(service: FakeProvider, playlist_id: str) -> list[str | None]:
    return [service.native_id(t) for t in service.playlists_by_id[playlist_id][1]]


def test_import_creates_the_playlist_and_reports_everything():
    service = FakeProvider(catalog=CATALOG)
    result = import_tracks(service, source_tracks(), "Mix")
    assert [m.method for m in result.matched] == ["isrc", "search"]
    assert [str(t) for t in result.unmatched] == ["Nobody - Nonexistent"]
    assert (result.duplicates, result.added, result.already_there) == (1, 2, 0)
    assert result.playlist_id is not None and ids_in(service, result.playlist_id) == ["1", "2"]


def test_running_it_again_adds_nothing():
    service = FakeProvider(catalog=CATALOG)
    import_tracks(service, source_tracks(), "Mix")
    again = import_tracks(service, source_tracks(), "mix")  # the name match ignores case
    assert (again.added, again.already_there) == (0, 2)
    assert service.add_calls == 1 and len(service.playlists_by_id) == 1


def test_only_the_new_tracks_are_added_to_an_existing_playlist():
    service = FakeProvider(catalog=CATALOG)
    service.existing_playlist("Mix", CATALOG[0])
    result = import_tracks(service, source_tracks(), "Mix")
    assert (result.added, result.already_there) == (1, 1)


def test_a_dry_run_changes_nothing():
    service = FakeProvider(catalog=CATALOG)
    result = import_tracks(service, source_tracks(), "Mix", dry_run=True)
    assert result.added == 2 and result.dry_run and service.playlists_by_id == {}


def test_a_playlist_that_is_not_yours_is_refused():
    service = FakeProvider(catalog=CATALOG, readable=False)
    service.create_playlist("Mix")
    with pytest.raises(ProviderError, match="not yours"):
        import_tracks(service, source_tracks(), "Mix")


def test_tracks_that_come_with_an_id_skip_the_lookup():
    service = FakeProvider()  # empty catalogue: only the id can match
    result = import_tracks(service, [Track("Known", ["A"], ids={"fake": "77"})], "Mix")
    assert [m.method for m in result.matched] == ["id"] and result.added == 1
    assert service.isrc_lookups == []


def test_isrcs_are_looked_up_in_bulk_per_chunk_of_twenty():
    tracks = [track(f"Song {i}", isrc=f"USAAA{i:07d}") for i in range(25)]
    service = FakeProvider()
    resolve_tracks(service, tracks)
    assert [len(codes) for codes in service.isrc_lookups] == [20, 5]


def test_one_track_that_cannot_be_looked_up_does_not_stop_the_run():
    class Flaky(FakeProvider):
        def search(self, query):
            if "Broken" in query:
                raise ApiError(400, "bad query")
            return super().search(query)

    matches, unmatched = resolve_tracks(
        Flaky(catalog=CATALOG), [Track("Broken", ["X"]), Track("Song B", ["Artist B"], duration_ms=180_000)]
    )
    assert len(matches) == 1 and len(unmatched) == 1


@pytest.mark.parametrize("error", [QuotaExceeded(429, "quota"), ApiError(500, "boom")])
def test_quota_and_server_errors_stop_the_run(error):
    class Broken(FakeProvider):
        def search(self, query):
            raise error

    with pytest.raises(ApiError):
        resolve_tracks(Broken(catalog=CATALOG), [Track("Song B", ["Artist B"])])


def test_progress_is_reported_for_every_track():
    seen = []
    resolve_tracks(
        FakeProvider(catalog=CATALOG),
        source_tracks(),
        progress=lambda done, total, t, m: seen.append((done, total, m is not None)),
    )
    assert seen == [(1, 4, True), (2, 4, True), (3, 4, False), (4, 4, True)]


class TestSelectTracks:
    def service(self) -> FakeProvider:
        service = FakeProvider(liked=[track("Liked", ids={"fake": "9"})])
        service.existing_playlist("Road trip", track("In list", ids={"fake": "8"}))
        return service

    def test_liked_songs(self):
        assert [(name, len(tracks)) for name, tracks in select_tracks(self.service(), liked=True)] == [
            ("Liked Songs", 1)
        ]

    def test_one_playlist_by_name_or_id(self):
        service = self.service()
        assert select_tracks(service, playlist="road TRIP")[0][0] == "Road trip"
        assert select_tracks(service, playlist="pl1")[0][0] == "Road trip"

    def test_everything_readable(self):
        assert [name for name, _ in select_tracks(self.service())] == ["Liked Songs", "Road trip"]

    def test_unreadable_playlists_are_skipped_with_a_note(self):
        service, notes = self.service(), []
        service.readable = False
        assert [name for name, _ in select_tracks(service, on_skip=notes.append)] == ["Liked Songs"]
        assert "Road trip" in notes[0]

    def test_a_missing_or_unreadable_playlist_is_an_error(self):
        service = self.service()
        with pytest.raises(ProviderError, match="No playlist"):
            select_tracks(service, playlist="nope")
        service.readable = False
        with pytest.raises(ProviderError, match="not yours"):
            select_tracks(service, playlist="Road trip")
