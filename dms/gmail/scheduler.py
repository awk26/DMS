"""In-process periodic Gmail synchronisation.

A single daemon thread runs ``sync_all`` (every connected mailbox) every
GMAIL_SYNC_INTERVAL_SECONDS while the application is running. "Sync now" wakes
the thread early. If several processes run the app, the per-mailbox database
lease in ``sync_account`` ensures each mailbox is synchronised by one of them at a time.
"""
import logging
import threading

from ..extensions import db

log = logging.getLogger(__name__)


class SyncScheduler:
    def __init__(self, app):
        self.app = app
        self.interval = app.config["GMAIL_SYNC_INTERVAL_SECONDS"]
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread = None
        self.running_now = False
        self.last_result = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, name="gmail-sync", daemon=True)
        self._thread.start()
        log.info("Gmail sync scheduler started (every %ss)", self.interval)

    def stop(self):
        self._stop.set()
        self._wake.set()

    def trigger(self):
        self._wake.set()

    @property
    def alive(self):
        return bool(self._thread and self._thread.is_alive())

    def run_once(self, trigger="scheduled"):
        from .sync import sync_all
        with self.app.app_context():
            self.running_now = True
            try:
                self.last_result = sync_all(trigger=trigger)
            except Exception:  # never let the thread die
                log.exception("Unexpected error in Gmail sync")
            finally:
                self.running_now = False
                db.session.remove()

    def _loop(self):
        self._stop.wait(5)  # let the app finish starting
        while not self._stop.is_set():
            triggered = self._wake.is_set()
            self._wake.clear()
            self.run_once("manual" if triggered else "scheduled")
            self._wake.wait(self.interval)
