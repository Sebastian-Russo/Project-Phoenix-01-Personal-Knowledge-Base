"""
On-demand Google Docs sync — triggered by an API call.

The simplest sync strategy:
- No background threads
- No scheduling
- Sync runs when the user clicks "Sync Now" in the dashboard
  or hits the /sync endpoint

Flow:
1. List all Google Docs in the configured folder
2. For each doc, fetch content and check if it changed
3. Ingest changed docs, skip unchanged ones
4. Return a SyncResult summarizing what happened

This is the right default for most personal use cases —
you control when syncing happens, there's no resource usage
when you're not actively syncing, and there's no risk of
a background thread silently failing.
"""

import time
from src.sync.base_sync          import BaseSync, SyncResult
from src.ingestion.ingester      import Ingester
from src.ingestion.gdocs_ingester import GDocsIngester


class ManualSync(BaseSync):
    """
    Runs a full sync when explicitly called.
    No background threads — purely on-demand.
    """

    def __init__(self, ingester: Ingester):
        self.ingester        = ingester
        self.gdocs           = ingester.gdocs_ingester
        self._last_sync:  str  = None
        self._last_result: SyncResult = None

    # ── BaseSync interface ─────────────────────────────────────────────────

    def sync(self, folder_id: str = None, tags: list[str] = None) -> SyncResult:
        """
        Run a full sync of the configured Google Drive folder.

        Lists all Google Docs, ingests new or changed ones,
        skips unchanged ones. One failure doesn't stop the rest.
        """
        result     = SyncResult(started_at=self._now())
        start_time = time.time()

        print(f"[ManualSync] Starting sync...")

        if not self.gdocs.is_authenticated():
            result.failed.append({
                "title": "Authentication",
                "error": "Not authenticated with Google. Visit /auth/google first."
            })
            result.duration_seconds = round(time.time() - start_time, 1)
            return result

        try:
            files = self.gdocs.list_folder(folder_id)
        except Exception as e:
            result.failed.append({"title": "Folder listing", "error": str(e)})
            result.duration_seconds = round(time.time() - start_time, 1)
            return result

        print(f"[ManualSync] Found {len(files)} Google Docs")

        for file in files:
            doc_id = file["id"]
            title  = file["name"]

            try:
                metadata = self.ingester.ingest_gdoc(
                    doc_id = doc_id,
                    tags   = tags or ["gdocs-sync"]
                )

                # DocumentStore returns existing metadata unchanged
                # when content hasn't changed — detect skip vs ingest
                # by checking if updated_at is very recent
                if self._was_just_ingested(metadata):
                    result.ingested.append(title)
                    print(f"[ManualSync] ✓ Ingested: {title}")
                else:
                    result.skipped.append(title)
                    print(f"[ManualSync] — Skipped (unchanged): {title}")

            except Exception as e:
                result.failed.append({"title": title, "error": str(e)})
                print(f"[ManualSync] ✗ Failed: {title} — {e}")

        result.duration_seconds = round(time.time() - start_time, 1)
        self._last_sync         = self._now()
        self._last_result       = result

        print(f"[ManualSync] {result.summary()}")
        return result

    def start(self) -> None:
        """No-op for manual sync — nothing to start."""
        print("[ManualSync] Mode: manual — sync runs on demand via /sync endpoint")

    def stop(self) -> None:
        """No-op for manual sync — nothing to stop."""
        pass

    def get_status(self) -> dict:
        return {
            "mode":        "manual",
            "last_sync":   self._last_sync,
            "is_running":  False,
            "last_result": self._last_result.summary() if self._last_result else None,
            "authenticated": self.gdocs.is_authenticated()
        }

    # ── Private ────────────────────────────────────────────────────────────

    def _was_just_ingested(self, metadata) -> bool:
        """
        Detect whether a document was freshly ingested vs skipped.

        DocumentStore.ingest() returns early with existing metadata
        when content is unchanged. We check if updated_at is within
        the last 60 seconds to distinguish a fresh ingest from a skip.
        """
        from datetime import datetime, timezone, timedelta

        try:
            updated = datetime.fromisoformat(metadata.updated_at)
            now     = datetime.now(timezone.utc)
            return (now - updated) < timedelta(seconds=60)
        except Exception:
            return True
