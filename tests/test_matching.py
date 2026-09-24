import pytest

from musicsync.matching import best_match, normalize, score, simplify_title
from musicsync.models import Track


def t(title: str, artists: tuple[str, ...] = ("Artist",), duration_ms: int | None = 200_000) -> Track:
    return Track(title, list(artists), duration_ms=duration_ms)


@pytest.mark.parametrize(
    ("title", "simplified", "normalized"),
    [
        ("Song (feat. Someone) - Remastered 2011", "Song", "song"),
        ("Sóng!", "Sóng!", "song"),
        ("Song - 2011 Remaster", "Song", "song"),
        ("Song [Deluxe Edition]", "Song", "song"),
    ],
)
def test_titles_lose_their_noise(title, simplified, normalized):
    assert simplify_title(title) == simplified
    assert normalize(title) == normalized


def test_identical_tracks_score_one():
    assert score(t("Song"), t("Song")) == 1.0


@pytest.mark.parametrize("candidate", ["Song (Remastered 2011)", "Song - 2011 Remaster", "Song (feat. X)"])
def test_remaster_and_featuring_do_not_matter(candidate):
    assert score(t("Song"), t(candidate)) >= 0.95


@pytest.mark.parametrize("candidate", ["Song - Live at Wembley", "Song (Acoustic)", "Song (Remix)"])
def test_another_version_is_not_the_same_track(candidate):
    assert score(t("Song"), t(candidate)) < 0.8


def test_a_different_artist_never_matches():
    assert score(t("Song", ("Artist",)), t("Song", ("Somebody Else",))) == 0.0


@pytest.mark.parametrize(("wanted", "found"), [("The Beatles", "Beatles"), ("Björk", "Bjork")])
def test_artist_spelling_is_forgiving(wanted, found):
    assert score(t("Song", (wanted,)), t("Song", (found,))) >= 0.8


def test_a_very_different_length_lowers_the_score():
    assert score(t("Song", duration_ms=400_000), t("Song")) < score(t("Song"), t("Song"))


def test_an_unknown_duration_is_neutral():
    assert score(t("Song", duration_ms=None), t("Song")) >= 0.95


def test_best_match_picks_the_highest_score_and_respects_the_threshold():
    match = best_match(t("Song"), [t("Something Else"), t("Song (Live)"), t("Song")])
    assert match is not None and match[0].title == "Song"
    assert best_match(t("Song"), [t("Something Else")]) is None
    assert best_match(t("Song"), []) is None


def a(title: str, *artists: str, duration_ms: int | None = 200_000, album: str = "") -> Track:
    return Track(title, list(artists), album, duration_ms)


# How one service writes a track, and how the other writes the same recording.
@pytest.mark.parametrize(
    ("wanted", "found"),
    [
        (a("Travesuras", "Nicky Jam x J Balvin"), a("Travesuras", "Nicky Jam", "J Balvin", duration_ms=206_000)),
        (a("Thrift Shop", "Macklemore & Ryan Lewis"), a("Thrift Shop (feat. Wanz)", "Macklemore", "Ryan Lewis")),
        (a("Don\u2019t Stop Me Now", "Queen"), a("Don't Stop Me Now (Remastered 2011)", "Queen")),
        (a("Rock 'n' Roll Star", "Oasis"), a("Rock and Roll Star", "Oasis")),
        (a("Alone, Pt. II", "Alan Walker"), a("Alone, Part II", "Alan Walker")),
        (a("Hotel California \u2013 2013 Remaster", "Eagles"), a("Hotel California (2013 Remaster)", "Eagles")),
        (
            a("(I Can't Get No) Satisfaction", "The Rolling Stones"),
            a("I Can't Get No (Satisfaction)", "Rolling Stones"),
        ),
        (a("TiK ToK", "Kesha"), a("TiK ToK", "Ke$ha")),
        (a("Thunderstruck", "AC/DC"), a("Thunderstruck", "ACDC")),
        (a("Symphony No. 5, Op. 67: I. Allegro", "Beethoven"), a("Symphony No. 5: I. Allegro, Op. 67", "Beethoven")),
        (a("Levels", "Avicii"), a("Levels (Original Mix)", "Avicii")),
        (a("Stay (with Justin Bieber)", "The Kid LAROI"), a("Stay", "The Kid LAROI", "Justin Bieber")),
        (a("Imagine - Remastered 2010", "John Lennon"), a("Imagine (Digitally Remastered)", "John Lennon")),
    ],
)
def test_the_same_recording_written_differently_matches(wanted, found):
    assert score(wanted, found) >= 0.9


@pytest.mark.parametrize(
    ("wanted", "found"),
    [
        (a("Love Story (Taylor\u2019s Version)", "Taylor Swift"), a("Love Story", "Taylor Swift")),
        (a("Snowfall", "Øneheart"), a("Snowfall (Sped Up)", "Øneheart")),
        (a("Lose Yourself", "Eminem"), a("Lose Yourself (Instrumental)", "Eminem")),
        (a("Someone Like You", "Adele"), a("Someone Like You (Karaoke Version)", "Adele")),
        (a("(Untitled)", "Sigur Rós"), a("[Intro]", "Sigur Rós")),  # both used to shrink to ""
    ],
)
def test_another_recording_does_not_match(wanted, found):
    assert score(wanted, found) < 0.8


def test_joined_artist_names_are_split_but_short_parts_are_ignored():
    assert score(a("Song", "Earth, Wind & Fire"), a("Song", "Earth, Wind & Fire")) == 1.0
    assert score(a("Old Town Road", "Lil Nas X"), a("Old Town Road", "X")) == 0.0  # "x" joins nothing here


def test_without_artists_the_title_and_length_decide():
    # A CSV with titles only: this used to be refused whatever the title, as the artist counted as a mismatch.
    assert score(a("Get Lucky", duration_ms=None), a("Get Lucky", "Daft Punk")) >= 0.9
    assert score(a("Get Lucky", "Daft Punk"), a("Get Lucky")) >= 0.9  # a result without artist data
    assert score(a("Get Lucky", duration_ms=None), a("Something Else", "Daft Punk")) < 0.8


def test_ties_go_to_the_closest_version_and_then_to_the_same_album():
    wanted = a("Summertime Sadness (Cedric Gervais Remix)", "Lana Del Rey")
    kaskade, cedric = a("Summertime Sadness (Kaskade Remix)", "Lana Del Rey"), a(wanted.title, "Lana Del Rey")
    assert best_match(wanted, [kaskade, cedric]) == (cedric, 1.0)

    wanted = a("Wonderwall", "Oasis", album="(What's The Story) Morning Glory?")
    hits, original = a("Wonderwall", "Oasis", album="Time Flies"), a("Wonderwall", "Oasis", album=wanted.album)
    assert best_match(wanted, [hits, original]) == (original, 1.0)


def test_only_the_version_part_of_a_title_names_a_version():
    assert score(t("Live Wire", ("AC/DC",)), t("Live Wire (Live)", ("AC/DC",))) < 0.8
    assert score(t("Live Forever", ("Oasis",)), t("Live Forever - Remastered", ("Oasis",))) == 1.0
    assert score(t("Remix to Ignition", ("R. Kelly",)), t("Remix to Ignition", ("R. Kelly",))) == 1.0


def test_a_title_of_only_brackets_is_kept():
    assert simplify_title("(Untitled)") == "(Untitled)"
