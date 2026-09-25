import pytest

from musicsync.errors import ApiError, ProviderError, QuotaExceeded
from musicsync.models import Track
from musicsync.providers.base import search_queries
from musicsync.sync import Step, import_tracks, resolve_tracks, select_tracks

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
    assert (
        service.descriptions[result.playlist_id] == "Imported with Music-Sync: https://github.com/Stensel8/Music-Sync"
    )


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

    matches, unmatched, _ = resolve_tracks(
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
    steps: list[Step] = []
    resolve_tracks(FakeProvider(catalog=CATALOG), source_tracks(), progress=steps.append)
    assert [(s.phase, s.done, s.total, s.match is not None) for s in steps] == [
        ("match", 1, 4, True),
        ("match", 2, 4, True),
        ("match", 3, 4, False),
        ("match", 4, 4, True),
    ]
    assert steps[0].text == "Finding the tracks on Fake" and steps[2].track == source_tracks()[2]


def test_a_track_that_came_close_is_reported_with_its_closest_candidate():
    near = Track("Song B (Live)", ["Artist B"], duration_ms=180_000)  # only the studio version is there
    steps: list[Step] = []
    matches, unmatched, near_misses = resolve_tracks(FakeProvider(catalog=CATALOG), [near], progress=steps.append)
    assert matches == [] and unmatched == [near]
    ((track, closest),) = near_misses
    assert track is near and closest.track.title == "Song B" and 0 < closest.score < 0.8
    assert steps[0].match is None and steps[0].closest == closest


def test_an_import_reports_each_step():
    service = FakeProvider(catalog=CATALOG)
    service.existing_playlist("Mix", CATALOG[0])
    steps: list[Step] = []
    import_tracks(service, source_tracks(), "Mix", progress=steps.append)
    assert [(s.phase, s.done, s.total) for s in steps if s.phase != "match"] == [
        ("check", 1, 1),
        ("add", 0, 1),
        ("add", 1, 1),
    ]
    assert steps[-1].text == "Adding to Mix on Fake"


def test_tracks_are_added_in_batches_with_progress_for_each():
    tracks = [track(f"Song {i}", ids={"fake": str(i)}) for i in range(5)]
    service = FakeProvider()
    service.add_batch = 2
    steps: list[Step] = []
    import_tracks(service, tracks, "Mix", progress=steps.append)
    assert service.add_calls == 3
    assert [s.done for s in steps if s.phase == "add"] == [0, 2, 4, 5]


# --- finding one track ----------------------------------------------------------------------------------


def test_the_searches_go_from_specific_to_loose_without_repeats():
    assert search_queries(Track("J\u00f3ga - Remastered", ["Bj\u00f6rk"])) == [
        "J\u00f3ga - Remastered Bj\u00f6rk",
        "J\u00f3ga Bj\u00f6rk",
        "joga bjork",
        "J\u00f3ga",
    ]
    assert search_queries(Track("Blinding Lights", ["The Weeknd"])) == ["Blinding Lights The Weeknd", "Blinding Lights"]
    assert search_queries(Track("Get Lucky")) == ["Get Lucky"]


class Scripted(FakeProvider):
    """Answers each search from a script, and remembers what was asked."""

    def __init__(self, answers: dict[str, list[Track] | Exception]):
        super().__init__()
        self.answers = answers
        self.asked: list[str] = []

    def search(self, query):
        self.asked.append(query)
        answer = self.answers.get(query, [])
        if isinstance(answer, Exception):
            raise answer
        return answer


def test_a_search_the_service_refuses_does_not_stop_the_other_searches():
    wanted = Track("Song (feat. X)", ["Artist"], duration_ms=200_000)
    right = track("Song", ids={"fake": "1"})
    service = Scripted({"Song (feat. X) Artist": ApiError(400, "bad query"), "Song Artist": [right]})
    match = service.find(wanted)
    assert match is not None and match.track is right and service.asked[:2] == ["Song (feat. X) Artist", "Song Artist"]


def test_the_best_hit_of_all_searches_wins_and_a_convincing_one_ends_the_search():
    wanted = Track("Song - Remastered", ["Artist"], duration_ms=200_000)
    live, studio = track("Song (Live)", ids={"fake": "1"}), track("Song", ids={"fake": "2"})
    service = Scripted({"Song - Remastered Artist": [live], "Song Artist": [studio]})
    match = service.find(wanted)
    assert match is not None and match.track is studio and match.score == 1.0
    assert service.asked == ["Song - Remastered Artist", "Song Artist"]  # no need for the looser searches


def test_find_returns_what_came_closest_even_when_it_is_not_good_enough():
    service = Scripted({"Song Artist": [track("Song (Live)", ids={"fake": "1"})]})
    match = service.find(Track("Song", ["Artist"], duration_ms=200_000))
    assert match is not None and match.score < 0.8
    assert Scripted({}).find(Track("Song", ["Artist"])) is None  # nothing at all


class TestSelectTracks:
    def service(self) -> FakeProvider:
        service = FakeProvider(liked=[track("Liked", ids={"fake": "9"})])
        service.existing_playlist("Road trip", track("In list", ids={"fake": "8"}))
        return service

    def test_liked_songs(self):
        assert [(name, len(tracks)) for name, tracks in select_tracks(self.service(), liked=True)] == [
            ("Liked Songs", 1)
        ]

    def test_reading_is_reported_with_the_total_the_service_gives(self):
        steps: list[Step] = []
        select_tracks(self.service(), playlist="Road trip", progress=steps.append)
        assert [(s.phase, s.text, s.done, s.total) for s in steps] == [("read", "Reading Road trip from Fake", 1, 1)]

    def test_an_unknown_or_wrong_total_is_corrected_at_the_end(self):
        service = self.service()
        steps: list[Step] = []
        select_tracks(service, liked=True, progress=steps.append)  # the fake does not know how many there are
        assert [(s.done, s.total) for s in steps] == [(1, None), (1, 1)]

        service.liked_count = lambda: 5  # type: ignore[method-assign]  # out of date
        steps.clear()
        select_tracks(service, liked=True, progress=steps.append)
        assert [(s.done, s.total) for s in steps] == [(1, 5), (1, 1)]

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

    def test_empty_ones_are_skipped_with_a_note_when_selecting_everything(self):
        service, notes = self.service(), []
        service.existing_playlist("Empty")
        assert [name for name, _ in select_tracks(service, on_skip=notes.append)] == ["Liked Songs", "Road trip"]
        assert notes == ["skipping 'Empty': it is empty"]

    def test_asking_for_an_empty_one_is_an_error(self):
        service = FakeProvider()
        service.existing_playlist("Empty")
        with pytest.raises(ProviderError, match='"Empty" is empty'):
            select_tracks(service, playlist="Empty")
        with pytest.raises(ProviderError, match='"Liked Songs" is empty'):
            select_tracks(service, liked=True)
