"""Single background worker. The GPU handles one reel at a time."""
import logging
import threading

from .pipeline import Pipeline

log = logging.getLogger(__name__)


class Worker:
    def __init__(self, pipeline: Pipeline, poll_s: float = 3.0):
        self.p = pipeline
        self.poll_s = poll_s
        self.stop_event = threading.Event()
        self.wake = threading.Event()
        self.current: int | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        n = self.p.db.reset_interrupted()
        if n:
            log.info("re-queued %d reels interrupted by a restart", n)
        self._thread = threading.Thread(target=self._loop, name="reel-worker", daemon=True)
        self._thread.start()

    def notify(self) -> None:
        self.wake.set()

    def stop(self) -> None:
        self.stop_event.set()
        self.wake.set()

    def _loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                reel = self.p.db.claim_next()
            except Exception:  # noqa: BLE001
                log.exception("could not read the queue")
                reel = None
            if reel is None:
                self.wake.wait(self.poll_s)
                self.wake.clear()
                continue
            self.current = reel["id"]
            try:
                self.p.process(reel)
            except Exception:  # noqa: BLE001 - process() records failures; this is a last resort
                log.exception("unexpected error on reel %s", reel["id"])
            finally:
                self.current = None
