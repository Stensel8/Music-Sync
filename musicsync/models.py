"""The service-neutral shapes every other module works with."""

import re
from dataclasses import dataclass, field
from typing import Literal

# ISO 3901: two letters (country), three letters or digits (registrant), seven digits (year and code).
_ISRC = re.compile(r"[A-Z]{2}[A-Z0-9]{3}\d{7}")


def normalize_isrc(value: str | None) -> str | None:
    """The canonical form of an ISRC ("gb-abc-12-34567" gives "GBABC1234567"), or None if it is not one."""
    cleaned = (value or "").replace("-", "").replace(" ", "").upper()
    return cleaned if _ISRC.fullmatch(cleaned) else None


@dataclass(slots=True)
class Track:
    """One recording. ``ids`` holds each service's own id for it, keyed by service name."""

    title: str
    artists: list[str] = field(default_factory=list)
    album: str = ""
    duration_ms: int | None = None
    isrc: str | None = None  # identifies the recording itself, so it is the best way to find it again
    ids: dict[str, str] = field(default_factory=dict)  # {"spotify": "spotify:track:...", "tidal": "123"}

    @property
    def artist(self) -> str:
        """The main (first) artist."""
        return self.artists[0] if self.artists else ""

    def __str__(self) -> str:
        return f"{', '.join(self.artists) or 'Unknown artist'} - {self.title}"


@dataclass(slots=True)
class PlaylistInfo:
    id: str
    name: str
    track_count: int | None = None
    readable: bool = True  # Spotify only shows the contents of playlists you own or collaborate on


@dataclass(slots=True)
class Match:
    """A track found on a service, and how it was found."""

    track: Track
    score: float
    method: Literal["id", "isrc", "search", "playlist"]  # "playlist": already in the target playlist, no lookup
