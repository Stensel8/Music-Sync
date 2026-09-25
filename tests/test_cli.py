import pytest

from musicsync import cli
from musicsync.config import Settings, config_path
from musicsync.csvio import read_tracks, write_tracks
from musicsync.errors import ApiError
from musicsync.models import Track
from musicsync.oauth import TokenStore
from musicsync.services import Services

from .support import ISRC_A, FakeProvider, FakeServices, track

CATALOG = [
    track("Song A", "Artist A", ids={"tidal": "1"}, isrc=ISRC_A),
    track("Song B", "Artist B", ids={"tidal": "2"}),
]


def test_export_liked_songs_to_csv(tmp_path, capsys):
    liked = [track("Song A", "Artist A", ids={"spotify": "spotify:track:1"}, isrc=ISRC_A, album="Album")]
    services = FakeServices(tmp_path, spotify=FakeProvider("spotify", liked=liked))
    out = tmp_path / "liked.csv"
    assert cli.main(["export", "spotify", "--liked", "-o", str(out)], services) == 0
    assert read_tracks(out) == liked
    assert "1 tracks" in capsys.readouterr().out


def test_export_all_writes_one_csv_per_playlist_and_skips_unreadable_ones(tmp_path, capsys):
    spotify = FakeProvider("spotify", liked=[track("Liked one")])
    spotify.existing_playlist("Road/trip: 2026", track("In playlist"))
    services = FakeServices(tmp_path, spotify=spotify)

    assert cli.main(["export", "spotify", "--all", "-o", str(tmp_path / "all")], services) == 0
    assert sorted(p.name for p in (tmp_path / "all").iterdir()) == ["Liked Songs.csv", "Road_trip_ 2026.csv"]

    spotify.readable = False
    assert cli.main(["export", "spotify", "--all", "-o", str(tmp_path / "all2")], services) == 0
    assert "skipping" in capsys.readouterr().err


def test_empty_lists_are_not_exported_and_are_flagged(tmp_path, capsys):
    spotify = FakeProvider("spotify", liked=[track("Liked one")])
    spotify.existing_playlist("Empty")
    services = FakeServices(tmp_path, spotify=spotify)

    assert cli.main(["export", "spotify", "--all", "-o", str(tmp_path / "all")], services) == 0
    assert [p.name for p in (tmp_path / "all").iterdir()] == ["Liked Songs.csv"]
    assert "skipping 'Empty': it is empty" in capsys.readouterr().err

    out = tmp_path / "empty.csv"
    assert cli.main(["export", "spotify", "--playlist", "Empty", "-o", str(out)], services) == 1
    assert not out.exists() and '"Empty" is empty' in capsys.readouterr().err


def test_transfer_flags_empty_playlists(tmp_path, capsys):
    spotify = FakeProvider("spotify", liked=[track("Liked one")])
    spotify.existing_playlist("Empty")
    services = FakeServices(tmp_path, spotify=spotify, tidal=FakeProvider("tidal", CATALOG))
    assert cli.main(["transfer", "spotify", "tidal", "--all", "-q"], services) == 0
    assert "skipping 'Empty': it is empty" in capsys.readouterr().err


def test_export_one_playlist_by_name(tmp_path):
    tidal = FakeProvider("tidal")
    tidal.existing_playlist("Mix", track("In playlist"))
    services = FakeServices(tmp_path, tidal=tidal)
    out = tmp_path / "mix.csv"
    assert cli.main(["export", "tidal", "--playlist", "mix", "-o", str(out)], services) == 0
    assert [t.title for t in read_tracks(out)] == ["In playlist"]
    assert cli.main(["export", "tidal", "--playlist", "missing", "-o", str(out)], services) == 1


def test_import_a_file_without_a_header_row(tmp_path, capsys):
    csv_file = tmp_path / "albums.csv"
    csv_file.write_text("Artist A,Song A\nNobody,Nothing\n", encoding="utf-8")
    tidal = FakeProvider("tidal", CATALOG)
    unmatched = tmp_path / "unmatched.csv"
    args = ["import", "tidal", str(csv_file), "--playlist", "Mix", "-q", "--unmatched", str(unmatched)]
    assert cli.main(args, FakeServices(tmp_path, tidal=tidal)) == 0
    assert [tidal.native_id(t) for t in tidal.playlists_by_id["pl1"][1]] == ["1"]
    assert "Mix: 1 matched, 1 not found; added 1" in capsys.readouterr().out
    assert [t.title for t in read_tracks(unmatched)] == ["Nothing"]


def test_import_dry_run_and_an_empty_file(tmp_path, capsys):
    csv_file = tmp_path / "in.csv"
    write_tracks(csv_file, [Track("Song A", ["Artist A"], isrc=ISRC_A)])
    tidal = FakeProvider("tidal", CATALOG)
    services = FakeServices(tmp_path, tidal=tidal)
    assert cli.main(["import", "tidal", str(csv_file), "--dry-run", "-q"], services) == 0
    assert tidal.playlists_by_id == {} and "would add 1" in capsys.readouterr().out

    empty = tmp_path / "empty.csv"
    empty.write_text("", encoding="utf-8")
    assert cli.main(["import", "tidal", str(empty)], services) == 1


def test_a_missing_csv_file_is_an_error_not_a_traceback(tmp_path, capsys):
    services = FakeServices(tmp_path, tidal=FakeProvider("tidal"))
    assert cli.main(["import", "tidal", str(tmp_path / "nope.csv")], services) == 1
    assert "error:" in capsys.readouterr().err


def test_transfer_between_services(tmp_path):
    source = FakeProvider("spotify", liked=[Track("Song A", ["Artist A"], isrc=ISRC_A)])
    target = FakeProvider("tidal", CATALOG)
    services = FakeServices(tmp_path, spotify=source, tidal=target)
    assert cli.main(["transfer", "spotify", "tidal", "--liked", "-q"], services) == 0
    assert [name for name, _ in target.playlists_by_id.values()] == ["Liked Songs (from Spotify)"]
    assert cli.main(["transfer", "spotify", "spotify", "--liked"], services) == 1


def test_a_transfer_shows_each_step_and_what_came_closest(tmp_path, capsys):
    liked = [Track("Song A", ["Artist A"], isrc=ISRC_A), Track("Song B (Live)", ["Artist B"], duration_ms=200_000)]
    tidal = FakeProvider("tidal", CATALOG)
    services = FakeServices(tmp_path, spotify=FakeProvider("spotify", liked=liked), tidal=tidal)
    assert cli.main(["transfer", "spotify", "tidal", "--liked"], services) == 0
    captured = capsys.readouterr()
    # Not a terminal, so one line per completed step instead of a line redrawn in place.
    assert captured.err.splitlines() == [
        "Reading Liked Songs from Spotify  [####################]  2/2",
        "Finding the tracks on Tidal  [####################]  2/2  1 found, 1 not found",
        "Adding to Liked Songs (from Spotify) on Tidal  [####################]  1/1",
    ]
    assert "  Artist B - Song B (Live): only another version; closest: Artist B - Song B" in captured.out


def test_on_a_terminal_the_line_is_redrawn_in_place(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr("sys.stderr.isatty", lambda: True)
    services = FakeServices(tmp_path, spotify=FakeProvider("spotify", liked=[track("One"), track("Two")]))
    assert cli.main(["export", "spotify", "--liked", "-o", str(tmp_path / "out.csv")], services) == 0
    lines = capsys.readouterr().err.split("\r")[1:]  # each drawing starts by going back to the start of the line
    # The fake does not say how many liked songs there are, so the count comes without a total at first.
    assert [line.rstrip() for line in lines] == [
        "Reading Liked Songs from Spotify  1 so far",
        "Reading Liked Songs from Spotify  2 so far",
        "Reading Liked Songs from Spotify  [####################]  2/2",
    ]
    assert lines[-1].endswith("\n")  # a complete step ends its line


def test_quiet_means_no_progress(tmp_path, capsys):
    services = FakeServices(tmp_path, spotify=FakeProvider("spotify", liked=[track("One")]))
    assert cli.main(["export", "spotify", "--liked", "-q", "-o", str(tmp_path / "out.csv")], services) == 0
    assert capsys.readouterr().err == ""


def test_sync_favorites_puts_liked_songs_in_the_favourites(tmp_path, capsys):
    tidal = FakeProvider("tidal", CATALOG)
    spotify = FakeProvider("spotify", liked=[Track("Song A", ["Artist A"], isrc=ISRC_A)])
    services = FakeServices(tmp_path, spotify=spotify, tidal=tidal)
    assert cli.main(["transfer", "spotify", "tidal", "--sync-favorites", "-q"], services) == 0
    assert tidal.playlists_by_id == {} and [tidal.native_id(t) for t in tidal.liked] == ["1"]
    assert "Liked Songs: 1 matched" in capsys.readouterr().out

    csv_file = tmp_path / "in.csv"
    write_tracks(csv_file, [Track("Song B", ["Artist B"])])
    assert cli.main(["import", "tidal", str(csv_file), "--to-favorites", "-q"], services) == 0
    assert [tidal.native_id(t) for t in tidal.liked] == ["1", "2"]
    assert cli.main(["transfer", "spotify", "tidal", "--sync-favorites", "--to-playlist", "X"], services) == 1


def test_missing_credentials_are_explained_not_a_crash(tmp_path, capsys):
    services = Services(Settings(), TokenStore(tmp_path / "tokens.json"))
    assert cli.main(["playlists", "spotify"], services) == 1
    assert "SPOTIFY_CLIENT_ID" in capsys.readouterr().err


def test_status_and_logout_work_without_any_setup(tmp_path, capsys):
    services = Services(Settings(), TokenStore(tmp_path / "tokens.json"))
    assert cli.main(["status"], services) == 0
    out = capsys.readouterr().out
    assert "MISSING" in out and "not logged in" in out
    assert cli.main(["logout", "tidal"], services) == 0


def test_status_says_where_the_settings_file_belongs(tmp_path, capsys):
    services = Services(Settings(), TokenStore(tmp_path / "tokens.json"))
    cli.main(["status"], services)
    assert f"settings are read from {config_path()} (no such file yet" in capsys.readouterr().out
    config_path().parent.mkdir(parents=True)
    config_path().write_text("", encoding="utf-8")
    cli.main(["status"], services)
    assert f"settings are read from {config_path()}\n" in capsys.readouterr().out


def test_the_first_run_makes_the_settings_file_and_says_so(capsys):
    assert cli.main(["status"]) == 0
    assert f"Created {config_path()}" in capsys.readouterr().err
    assert config_path().exists()
    assert cli.main(["status"]) == 0
    assert "Created" not in capsys.readouterr().err


def test_doctor_reports_failures_with_a_nonzero_exit(tmp_path, capsys):
    class Denied(FakeProvider):
        def lookup_isrcs(self, isrcs):
            raise ApiError(403, "Forbidden")

    tidal = Denied("tidal", CATALOG, liked=[track("Song A", "Artist A", ids={"tidal": "1"}, isrc=ISRC_A)])
    assert cli.main(["doctor", "tidal"], FakeServices(tmp_path, tidal=tidal)) == 1
    out = capsys.readouterr().out
    assert "ok    read liked songs" in out and "FAIL  look up by ISRC: HTTP 403: Forbidden" in out


def test_a_mistyped_command_gets_a_suggestion(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["tranfser", "spotify", "tidal"])
    assert exc.value.code == 2
    assert "transfer" in capsys.readouterr().err


def test_help_lists_every_command(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"])
    assert exc.value.code == 0
    text = capsys.readouterr().out
    assert all(
        command in text
        for command in ("login", "logout", "status", "playlists", "export", "import", "transfer", "web", "doctor")
    )
