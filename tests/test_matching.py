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
