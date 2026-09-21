"""Decide whether a search result is the track we want. The fallback for tracks that an ISRC cannot find."""

import re
import unicodedata
from difflib import SequenceMatcher

from .models import Track

# A live version is not the studio version: when only one side has such a word, the title score is halved.
VERSION_TAGS = ("live", "remix", "acoustic", "instrumental", "demo", "karaoke", "cover", "radio edit", "extended")

_BRACKETS = re.compile(r"[(\[{][^)\]}]*[)\]}]")
_FEATURING = re.compile(r"\s+(?:feat\.?|ft\.?|featuring)\s+.*$", re.IGNORECASE)
_DASH_SUFFIX = re.compile(r"\s+-\s+.*$")  # "Song - Remastered 2011"
_NOISE = re.compile(
    r"\b(?:\d{4}\s+)?(?:digital\s+)?remaster(?:ed)?(?:\s+\d{4})?\b|\bmono\b|\bstereo\b|\bbonus track\b"
    r"|\bdeluxe(?: edition)?\b|\bexplicit\b|\b(?:single|album) version\b",
    re.IGNORECASE,
)

# Weights of the three signals; they add up to 1.
TITLE_WEIGHT, ARTIST_WEIGHT, DURATION_WEIGHT = 0.5, 0.4, 0.1
# Below this the artist is simply someone else, whatever the title says.
MIN_ARTIST_SCORE = 0.6


def _fold(text: str) -> str:
    """Lowercase and strip accents, so "Björk" equals "bjork"."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def _words(text: str) -> str:
    """Only the words of ``text``: punctuation gone, whitespace collapsed."""
    return " ".join(re.sub(r"[^\w\s]", " ", text).split())


def simplify_title(title: str) -> str:
    """Drop bracketed parts, "feat." clauses and " - Remastered" style suffixes, keeping the case."""
    title = _BRACKETS.sub(" ", title)
    title = _DASH_SUFFIX.sub("", title)
    return " ".join(_FEATURING.sub("", title).split())


def normalize(title: str) -> str:
    """The comparable form of a title."""
    return _words(_NOISE.sub(" ", _fold(simplify_title(title))))


def _tags(title: str) -> frozenset[str]:
    folded = _fold(title)
    return frozenset(tag for tag in VERSION_TAGS if re.search(rf"\b{tag}\b", folded))


def _artist_score(wanted: list[str], candidate: list[str]) -> float:
    """1.0 when any wanted artist equals any candidate artist, else the best similarity."""
    if not wanted or not candidate:
        return 0.5  # unknown: neither confirms nor rules out
    pairs = [(_words(_fold(w)), _words(_fold(c))) for w in wanted for c in candidate]
    return max(1.0 if a == b else SequenceMatcher(None, a, b).ratio() for a, b in pairs)


def _duration_score(a: int | None, b: int | None) -> float:
    if a is None or b is None:
        return 0.8  # unknown: slightly below a confirmed match
    difference = abs(a - b)
    return 1.0 if difference <= 3000 else 0.5 if difference <= 10_000 else 0.0


def score(wanted: Track, candidate: Track) -> float:
    """How well ``candidate`` matches ``wanted``, from 0 to 1."""
    artist = _artist_score(wanted.artists, candidate.artists)
    if artist < MIN_ARTIST_SCORE:
        return 0.0
    title = SequenceMatcher(None, normalize(wanted.title), normalize(candidate.title)).ratio()
    if _tags(wanted.title) != _tags(candidate.title):
        title *= 0.5
    duration = _duration_score(wanted.duration_ms, candidate.duration_ms)
    return round(TITLE_WEIGHT * title + ARTIST_WEIGHT * artist + DURATION_WEIGHT * duration, 4)


def best_match(wanted: Track, candidates: list[Track], min_score: float = 0.8) -> tuple[Track, float] | None:
    """The best candidate and its score, if it is good enough."""
    scored = [(candidate, score(wanted, candidate)) for candidate in candidates]
    best = max(scored, key=lambda pair: pair[1], default=None)
    return best if best and best[1] >= min_score else None
