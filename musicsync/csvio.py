"""Read and write track lists as CSV."""

import csv
import re
from collections.abc import Iterable
from pathlib import Path
from typing import TextIO

from .errors import CsvError
from .models import Track, normalize_isrc

COLUMNS = ("title", "artists", "album", "duration_ms", "isrc", "spotify_uri", "tidal_id")
ARTIST_SEPARATOR = "; "

# Every header spelling we understand, mapped to the column it means.
_ALIASES = {
    alias: column
    for column, aliases in {
        "title": ("title", "track name", "track", "name", "song"),
        "artists": ("artists", "artist", "artist name(s)", "artist names", "artist name"),
        "album": ("album", "album name"),
        "duration_ms": ("duration_ms", "duration (ms)", "duration"),
        "isrc": ("isrc",),
        "spotify_uri": ("spotify_uri", "track uri", "spotify uri", "uri"),
        "tidal_id": ("tidal_id", "tidal id"),
    }.items()
    for alias in aliases
}


def slug(name: str) -> str:
    """A file name for a playlist name: keeps letters, digits, spaces and dashes."""
    return re.sub(r"[^\w\- ]+", "_", name).strip(" _") or "playlist"


def read_tracks(path: str | Path) -> list[Track]:
    with Path(path).open(encoding="utf-8-sig", newline="") as fh:  # utf-8-sig: Excel adds a BOM
        return parse_csv(fh, source=str(path))


def parse_csv(fh: TextIO, source: str = "the CSV") -> list[Track]:
    """The tracks in a CSV; rows without a title are skipped."""
    try:
        rows = [row for row in csv.reader(fh) if any(cell.strip() for cell in row)]
    except UnicodeDecodeError as exc:
        raise CsvError(f"{source} is not UTF-8 encoded") from exc
    if not rows:
        return []

    header = [_ALIASES.get(cell.strip().lower()) for cell in rows[0]]
    if "title" in header:
        columns, body = header, rows[1:]
    else:
        columns, body = ["artists", "title"], rows  # no header row: "artist,title"
    records = ({c: v.strip() for c, v in zip(columns, row, strict=False) if c} for row in body)
    return [track for record in records if (track := _track(record))]


def _track(record: dict[str, str]) -> Track | None:
    if not (title := record.get("title")):
        return None
    ids = {}
    if record.get("spotify_uri", "").startswith("spotify:track:"):
        ids["spotify"] = record["spotify_uri"]
    if record.get("tidal_id"):
        ids["tidal"] = record["tidal_id"]
    try:
        duration = int(float(record.get("duration_ms", "")))
    except ValueError:
        duration = None
    return Track(
        title=title,
        artists=[a.strip() for a in record.get("artists", "").split(";") if a.strip()],
        album=record.get("album", ""),
        duration_ms=duration,
        isrc=normalize_isrc(record.get("isrc")),
        ids=ids,
    )


def write_tracks(path: str | Path, tracks: Iterable[Track]) -> int:
    """Write ``tracks`` to ``path`` (creating folders as needed); returns how many were written."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        return write_csv(fh, tracks)


def write_csv(fh: TextIO, tracks: Iterable[Track]) -> int:
    writer = csv.writer(fh)
    writer.writerow(COLUMNS)
    rows = [
        (
            t.title,
            ARTIST_SEPARATOR.join(t.artists),
            t.album,
            "" if t.duration_ms is None else t.duration_ms,
            t.isrc or "",
            t.ids.get("spotify", ""),
            t.ids.get("tidal", ""),
        )
        for t in tracks
    ]
    writer.writerows(rows)
    return len(rows)
