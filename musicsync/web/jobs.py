"""Background jobs, so a long export, import or transfer does not block a browser request."""

import logging
import secrets
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..errors import MusicSyncError
from ..models import Match, Track
from ..sync import Phase, Step, remaining

log = logging.getLogger(__name__)

# The steps each kind of job goes through, in order. The page shows them as a row of numbered steps.
PHASES: dict[str, tuple[Phase, ...]] = {
    "export": ("read",),
    "import": ("match", "check", "add"),
    "transfer": ("read", "match", "check", "add"),
}
SHOWN_MISSES = 50  # tracks not found that the page lists; the CSV download has them all


@dataclass(slots=True)
class Job:
    id: str
    kind: str  # export, import or transfer
    title: str  # "Transfer from Spotify to Tidal"
    status: str = "running"  # running, done or error
    phase: Phase | None = None
    text: str = "Starting"  # what is happening now
    done: int = 0
    total: int | None = None  # None while it is not known
    current: str = ""  # the track being looked up
    found: int = 0
    # The tracks not found so far, each with the candidate that came closest, if there was one.
    misses: list[tuple[Track, Match | None]] = field(default_factory=list)
    message: str = ""  # the summary, or what went wrong
    download: tuple[str, str] | None = None  # an export's file name and CSV text, once it is done
    started: float = field(default_factory=time.monotonic)
    phase_started: float = field(default_factory=time.monotonic)
    finished: float | None = None

    def progress(self, step: Step) -> None:
        """The progress callback of ``sync``. The page polls what it sets."""
        if step.phase != self.phase:
            self.phase, self.phase_started, self.current = step.phase, time.monotonic(), ""
        self.text, self.done, self.total = step.text, step.done, step.total
        if step.phase == "match" and step.track:
            self.current = str(step.track)
            if step.match:
                self.found += 1
            else:
                self.misses.append((step.track, step.closest))

    @property
    def unmatched(self) -> list[Track]:
        return [track for track, _ in self.misses]

    def to_json(self) -> dict[str, Any]:
        """Everything the page shows about the job."""
        now = self.finished or time.monotonic()
        eta = remaining(self.done, self.total, now - self.phase_started) if self.status == "running" else None
        return {
            "kind": self.kind,
            "title": self.title,
            "status": self.status,
            "phases": PHASES[self.kind],
            "phase": self.phase,
            "text": self.text,
            "done": self.done,
            "total": self.total,
            "current": self.current,
            "found": self.found,
            "not_found": len(self.misses),
            "elapsed": round(now - self.started, 1),
            "eta": None if eta is None else round(eta),
            "message": self.message,
            "unmatched": [
                {
                    "track": str(track),
                    "closest": str(closest.track) if closest else None,
                    "score": closest.score if closest else None,
                }
                for track, closest in self.misses[:SHOWN_MISSES]
            ],
            "unmatched_count": len(self.misses),
            "download": self.download is not None,
        }


class JobManager:
    """Runs jobs on threads and remembers the most recent ones."""

    def __init__(self, keep: int = 20):
        self._jobs: OrderedDict[str, Job] = OrderedDict()
        self._lock = threading.Lock()
        self._keep = keep

    def start(self, kind: str, title: str, work: Callable[[Job], str]) -> Job:
        """Run ``work(job)`` on a thread. What it returns is the summary shown once the job is done."""
        job = Job(id=secrets.token_urlsafe(8), kind=kind, title=title)
        with self._lock:
            self._jobs[job.id] = job
            while len(self._jobs) > self._keep:
                self._jobs.popitem(last=False)  # forget the oldest
        threading.Thread(target=self._run, args=(job, work), daemon=True).start()
        return job

    @staticmethod
    def _run(job: Job, work: Callable[[Job], str]) -> None:
        try:
            job.message = work(job)
            status = "done"
        except MusicSyncError as exc:
            job.message, status = str(exc), "error"
        except Exception:
            log.exception("Job %s failed", job.id)  # a bug: the details go to the log, not to the browser
            job.message, status = "Unexpected error; see the terminal for details.", "error"
        job.finished = time.monotonic()
        job.status = status  # last: everything else is set before the page sees the job end

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)
