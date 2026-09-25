import pytest

from musicsync.csvio import COLUMNS, read_tracks, slug, write_tracks
from musicsync.models import Track, normalize_isrc

from .support import ISRC_A


def write(tmp_path, text: str, encoding: str = "utf-8"):
    path = tmp_path / "in.csv"
    path.write_text(text, encoding=encoding)
    return path


def test_round_trip(tmp_path):
    tracks = [
        Track(
            "Get Lucky",
            ["Daft Punk", "Pharrell Williams"],
            "Random Access Memories",
            248000,
            ISRC_A,
            {"spotify": "spotify:track:abc"},
        ),
        Track('Comma, "quotes" and ünïcode', ["Tyler, The Creator"], ids={"tidal": "42"}),
    ]
    path = tmp_path / "out" / "tracks.csv"  # the folder is created
    assert write_tracks(path, tracks) == 2
    assert path.read_text(encoding="utf-8").splitlines()[0] == ",".join(COLUMNS)
    assert read_tracks(path) == tracks


def test_a_file_without_a_header_row_is_artist_then_title(tmp_path):
    tracks = read_tracks(write(tmp_path, "Daft Punk,Get Lucky\nBjörk,Jóga\n"))
    assert [(t.artists, t.title) for t in tracks] == [(["Daft Punk"], "Get Lucky"), (["Björk"], "Jóga")]


def test_rows_with_the_wrong_column_count_are_skipped_not_fatal(tmp_path):
    assert [t.title for t in read_tracks(write(tmp_path, "Only one column\nA,B,extra column\n\n"))] == ["B"]


def test_column_names_of_other_exporters_are_understood(tmp_path):
    text = "Track URI,Track Name,Album Name,Artist Name(s),Duration (ms)\nspotify:track:xyz,Song,Album,A;B,201000\n"
    (t,) = read_tracks(write(tmp_path, text, "utf-8-sig"))  # Excel adds a BOM
    assert (t.title, t.artists, t.album, t.duration_ms, t.ids) == (
        "Song",
        ["A", "B"],
        "Album",
        201000,
        {"spotify": "spotify:track:xyz"},
    )


def test_a_header_with_only_artist_and_title(tmp_path):
    assert [(t.artists, t.title) for t in read_tracks(write(tmp_path, "artist,title\nA,B\n"))] == [(["A"], "B")]


def test_a_list_of_albums_takes_the_album_as_title(tmp_path):
    assert [(t.artists, t.title) for t in read_tracks(write(tmp_path, "Artist,Album\nA,B\n"))] == [(["A"], "B")]


def test_junk_values_are_ignored(tmp_path):
    text = "title,artists,duration_ms,isrc,spotify_uri\nX,Y,not-a-number,ISRC1,https://example.com\n"
    (t,) = read_tracks(write(tmp_path, text))
    assert (t.duration_ms, t.isrc, t.ids) == (None, None, {})


def test_an_empty_file_has_no_tracks(tmp_path):
    assert read_tracks(write(tmp_path, "")) == []


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("gb-abc-12-34567", "GBABC1234567"),
        (" usaaa0000001 ", "USAAA0000001"),
        ("ISRC1", None),
        ("", None),
        (None, None),
    ],
)
def test_normalize_isrc(raw, expected):
    assert normalize_isrc(raw) == expected


@pytest.mark.parametrize(
    ("name", "expected"), [("Road/trip: 2026", "Road_trip_ 2026"), ("...", "playlist"), ("Ünï", "Ünï")]
)
def test_slug(name, expected):
    assert slug(name) == expected
