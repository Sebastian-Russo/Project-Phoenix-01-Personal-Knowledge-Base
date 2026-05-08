"""
Abstract base class for Google Docs sync strategies.

Defines the interface that both manual and realtime sync
must implement. The rest of the app only talks to this
interface — it never imports ManualSync or RealtimeSync
directly. This is what allows the GOOGLE_SYNC_MODE flag
in .env to swap strategies without changing any other code.

Think of it like a power outlet — the interface (two holes,
specific voltage) is fixed. What generates the power behind
the wall (manual crank vs power grid) can change without
affecting anything plugged into it.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class SyncResult:
    """
    Result of a sync operation.

    ingested:  documents successfully added or updated
    skipped:   documents that hadn't changed since last sync
    failed:    documents that failed to ingest with error messages
    started_at:  when the sync started
    duration_seconds: how long it took
    """
    ingested:         list[str] = field(default_factory=list)  # doc titles
    skipped:          list[str] = field(default_factory=list)
    failed:           list[dict] = field(default_factory=list) # {title, error}
    started_at:       str = ""
    duration_seconds: float = 0.0

    @property
    def total(self) -> int:
        return len(self.ingested) + len(self.skipped) + len(self.failed)

    @property
    def success_rate(self) -> float:
        if not self.total:
            return 0.0
        return round(len(self.ingested) / self.total, 2)

    def summary(self) -> str:
        return (
            f"Sync complete — "
            f"{len(self.ingested)} ingested, "
            f"{len(self.skipped)} skipped, "
            f"{len(self.failed)} failed "
            f"({self.duration_seconds:.1f}s)"
        )


class BaseSync(ABC):
    """
    Abstract sync interface.

    Both ManualSync and RealtimeSync implement these methods.
    The app always references BaseSync — never the concrete classes.
    """

    @abstractmethod
    def sync(self, folder_id: str = None, tags: list[str] = None) -> SyncResult:
        """
        Run a sync operation.

        folder_id: optional Drive folder to sync (overrides config)
        tags:      optional tags to apply to all synced documents

        Returns a SyncResult describing what happened.
        """
        pass

    @abstractmethod
    def start(self) -> None:
        """
        Start the sync process.
        For manual sync: no-op (sync runs on demand).
        For realtime sync: starts the background polling thread.
        """
        pass

    @abstractmethod
    def stop(self) -> None:
        """
        Stop the sync process.
        For manual sync: no-op.
        For realtime sync: stops the background polling thread.
        """
        pass

    @abstractmethod
    def get_status(self) -> dict:
        """
        Return current sync status.

        Should always return a dict with at least:
        {
            "mode":        "manual" | "realtime",
            "last_sync":   ISO timestamp or None,
            "is_running":  bool,
            "last_result": SyncResult summary or None
        }
        """
        pass

    # ── Shared utilities ───────────────────────────────────────────────────

    def _now(self) -> str:
        """Current UTC time as ISO 8601 string."""
        return datetime.now(timezone.utc).isoformat()

    def _elapsed(self, started_at: str) -> float:
        """Seconds elapsed since started_at ISO timestamp."""
        start  = datetime.fromisoformat(started_at)
        now    = datetime.now(timezone.utc)
        return round((now - start).total_seconds(), 1)

# Short by design — the base class only defines the contract.
# Three things it enforces on every sync implementation:
# a sync() method that does the actual work,
# start()/stop() for lifecycle management,
# and get_status() so the dashboard always knows what's happening regardless of which mode is active.
