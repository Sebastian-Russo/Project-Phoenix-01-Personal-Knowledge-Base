"""
Background polling sync — automatically ingests new and changed
Google Docs on a configurable interval.

How it works:
- A background thread wakes up every GOOGLE_SYNC_INTERVAL seconds
- It asks Google "what docs changed since I last checked?"
- It ingests only the changed docs — not the entire folder
- It goes back to sleep

Think of it like a security guard doing rounds:
- They don't watch every door simultaneously (too expensive)
- They walk the perimeter every N minutes (polling interval)
- They only investigate doors that look different (changed docs)
- They log what they found each round (SyncResult)

The key efficiency: get_modified_since() asks Google for only
the docs that changed since the last poll. A folder with 500
docs that nobody edited generates zero API calls per poll cycle.

Tradeoff vs manual sync:
- Pro: changes appear automatically without user action
- Pro: new docs added to the folder are picked up automatically
- Con: uses resources continuously (thread + API calls)
- Con: if the thread crashes silently, sync stops without warning
  (we handle this with a watchdog status check)
"""

import time
import threading
from datetime import datetime, timezone, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from src.sync.base_sync          import BaseSync, SyncResult
from src.ingestion.ingester      import Ingester
from config import GOOGLE_SYNC_INTERVAL, GOOGLE_SYNC_FOLDER_ID


class RealtimeSync(BaseSync):
    """
    Polls Google Drive on a background thread at a fixed interval.
    Uses APScheduler to manage the polling job lifecycle.
    """

    def __init__(self, ingester: Ingester):
        self.ingester          = ingester
        self.gdocs             = ingester.gdocs_ingester
        self.interval          = GOOGLE_SYNC_INTERVAL

        self._scheduler        = BackgroundScheduler()
        self._is_running       = False
        self._last_sync:  str  = None
        self._last_result: SyncResult = None
        self._last_checked: str = None   # timestamp of last successful poll
        self._lock             = threading.Lock()   # prevent overlapping syncs

    # ── BaseSync interface ─────────────────────────────────────────────────

    def sync(self, folder_id: str = None, tags: list[str] = None) -> SyncResult:
        """
        Run one sync cycle — called by APScheduler on the interval,
        or manually triggered via the /sync endpoint.

        Only fetches docs modified since _last_checked.
        First run fetches everything (no previous checkpoint).
        """
        # Prevent two sync cycles from running simultaneously
        # if a poll cycle takes longer than the interval
        if not self._lock.acquire(blocking=False):
            print("[RealtimeSync] Sync already in progress — skipping this cycle")
            return SyncResult(started_at=self._now())

        result     = SyncResult(started_at=self._now())
        start_time = time.time()

        try:
            print(f"[RealtimeSync] Poll cycle starting...")

            if not self.gdocs.is_authenticated():
                result.failed.append({
                    "title": "Authentication",
                    "error": "Not authenticated with Google."
                })
                return result

            # ── Fetch changed docs ─────────────────────────────
            if self._last_checked:
                # Incremental — only docs changed since last poll
                files = self.gdocs.get_modified_since(
                    since_iso = self._last_checked,
                    folder_id = folder_id or GOOGLE_SYNC_FOLDER_ID
                )
            else:
                # First run — fetch everything in the folder
                print("[RealtimeSync] First run — fetching all docs")
                files = self.gdocs.list_folder(
                    folder_id = folder_id or GOOGLE_SYNC_FOLDER_ID
                )

            # Record the poll time BEFORE ingesting so we don't miss
            # docs that are modified during a long ingest run
            poll_time = self._now()

            if not files:
                print(f"[RealtimeSync] No changes since {self._last_checked}")
                self._last_checked = poll_time
                return result

            print(f"[RealtimeSync] {len(files)} docs to process")

            # ── Ingest changed docs ────────────────────────────
            for file in files:
                doc_id = file["id"]
                title  = file["name"]

                try:
                    metadata = self.ingester.ingest_gdoc(
                        doc_id = doc_id,
                        tags   = tags or ["gdocs-sync", "auto-synced"]
                    )

                    if self._was_just_ingested(metadata):
                        result.ingested.append(title)
                        print(f"[RealtimeSync] ✓ Ingested: {title}")
                    else:
                        result.skipped.append(title)

                except Exception as e:
                    result.failed.append({"title": title, "error": str(e)})
                    print(f"[RealtimeSync] ✗ Failed: {title} — {e}")

            # Advance checkpoint to poll time
            self._last_checked = poll_time

        finally:
            result.duration_seconds = round(time.time() - start_time, 1)
            self._last_sync         = self._now()
            self._last_result       = result
            self._lock.release()

        if result.ingested or result.failed:
            print(f"[RealtimeSync] {result.summary()}")

        return result

    def start(self) -> None:
        """
        Start the background polling scheduler.
        Called once at Flask app startup when GOOGLE_SYNC_MODE=realtime.
        """
        if self._is_running:
            print("[RealtimeSync] Already running")
            return

        self._scheduler.add_job(
            func          = self.sync,
            trigger       = "interval",
            seconds       = self.interval,
            id            = "gdocs_sync",
            replace_existing = True,
            max_instances = 1    # never run two cycles simultaneously
        )

        self._scheduler.start()
        self._is_running = True

        print(
            f"[RealtimeSync] Started — polling every {self.interval}s "
            f"({self.interval // 60}m {self.interval % 60}s)"
        )

        # Run first sync immediately rather than waiting for first interval
        threading.Thread(target=self.sync, daemon=True).start()

    def stop(self) -> None:
        """
        Stop the background polling scheduler.
        Called on Flask app shutdown.
        """
        if not self._is_running:
            return

        self._scheduler.shutdown(wait=False)
        self._is_running = False
        print("[RealtimeSync] Stopped")

    def get_status(self) -> dict:
        """
        Return current sync status including health indicators.

        is_stale: True if last successful sync was more than
        2x the interval ago — indicates the scheduler may have died.
        """
        is_stale = False

        if self._last_sync and self._is_running:
            last   = datetime.fromisoformat(self._last_sync)
            now    = datetime.now(timezone.utc)
            cutoff = timedelta(seconds=self.interval * 2)
            is_stale = (now - last) > cutoff

        return {
            "mode":          "realtime",
            "interval":      self.interval,
            "last_sync":     self._last_sync,
            "last_checked":  self._last_checked,
            "is_running":    self._is_running,
            "is_stale":      is_stale,
            "last_result":   self._last_result.summary() if self._last_result else None,
            "authenticated": self.gdocs.is_authenticated()
        }

    # ── Private ────────────────────────────────────────────────────────────

    def _was_just_ingested(self, metadata) -> bool:
        """Same logic as ManualSync — detect fresh ingest vs skip."""
        try:
            updated = datetime.fromisoformat(metadata.updated_at)
            now     = datetime.now(timezone.utc)
            return (now - updated) < timedelta(seconds=60)
        except Exception:
            return True

# Two things worth noting. The _lock.acquire(blocking=False) prevents overlapping sync cycles
# — if a poll takes longer than the interval (slow network, lots of changed docs) the next scheduled cycle skips
# rather than running simultaneously and causing race conditions on the vector store.
# The is_stale flag in get_status() is a lightweight health check — if the last sync was more than 2x the interval ago
# and the scheduler claims to be running, something went wrong. The dashboard can surface this as a warning
# so you know realtime sync silently died rather than discovering it days later when your KB is out of date.
