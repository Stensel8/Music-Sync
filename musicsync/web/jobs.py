"""Background jobs, so a long import or transfer does not block a browser request."""

import logging
import secrets
import threading
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field

from ..errors import MusicSyncError
from ..models import Match, Track

log = logging.getLogger(__name__)


@dataclass(slots=True)
class Job:
    id: str
    status: str = "running"  # running, done or error
    done: int = 0
    total: int = 0
    current: str = ""
    message: str = ""
    unmatched: list[Track] = field(default_factory=list)

    def progress(self, done: int, total: int, track: Track, _match: Match | None) -> None:
        """The progress callback of ``sync.import_tracks``. The page polls these numbers."""
        self.done, self.total, self.current = done, total, str(track)


class JobManager:
    """Runs jobs on threads and remembers the most recent ones."""

    def __init__(self, keep: int = 20):
        self._jobs: OrderedDict[str, Job] = OrderedDict()
        self._lock = threading.Lock()
        self._keep = keep

    def start(self, work: Callable[[Job], str]) -> Job:
        """Run ``work(job)`` on a thread. What it returns is the summary shown once the job is done."""
        job = Job(id=secrets.token_urlsafe(8))
        with self._lock:
            self._jobs[job.id] = job
            while len(self._jobs) > self._keep:
                self._jobs.popitem(last=False)  # forget the oldest
        threading.Thread(target=self._run, args=(job, work), daemon=True).start()
        return job

    @staticmethod
    def _run(job: Job, work: Callable[[Job], str]) -> None:
        try:
            job.message, job.status = work(job), "done"  # the message is set before the page sees "done"
        except MusicSyncError as exc:
            job.message, job.status = str(exc), "error"
        except Exception:
            log.exception("Job %s failed", job.id)  # a bug: the details go to the log, not to the browser
            job.message, job.status = "Onverwachte fout; kijk in de terminal voor details.", "error"

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)
