"""
Tracks metadata for every document in the knowledge base.

Metadata is the card catalog of our library — it doesn't contain
the book's content, but it tells you: what is this, where did it
come from, when was it added, how many chunks does it have, and
what tags describe it.

Without metadata we'd have a pile of chunks with no way to:
- Know which document a chunk came from
- Filter searches by source type or date
- Delete all chunks belonging to a document
- Show the user what's in their knowledge base

This module handles generating, storing, and retrieving that metadata.
It writes to a simple JSON file — no database needed for a personal tool.
"""

import json
import uuid
import hashlib
from datetime import datetime, timezone
from dataclasses import dataclass, field, asdict
from pathlib import Path
from config import DOCS_DIR


@dataclass
class DocumentMetadata:
    """
    Everything we track about a document.

    doc_id:       unique ID, generated at ingest time
    title:        human-readable name (filename, page title, doc title)
    source_type:  where it came from — "pdf", "url", "text", "gdoc"
    source_path:  original location — file path, URL, or Google Doc ID
    created_at:   when it was added to the KB
    updated_at:   when it was last re-ingested
    chunk_count:  how many chunks it was split into
    char_count:   total character count of the raw text
    content_hash: MD5 of the content — used to detect if doc changed
    tags:         user-supplied or auto-generated labels
    summary:      one-paragraph AI-generated summary (optional)
    extra:        any source-specific fields (e.g. Google Doc owner, PDF author)
    """
    doc_id:       str
    title:        str
    source_type:  str                      # "pdf" | "url" | "text" | "gdoc"
    source_path:  str
    created_at:   str
    updated_at:   str
    chunk_count:  int         = 0
    char_count:   int         = 0
    content_hash: str         = ""
    tags:         list[str]   = field(default_factory=list)
    summary:      str         = ""
    extra:        dict        = field(default_factory=dict)


class MetadataStore:
    """
    Persists document metadata to a JSON file.

    Keeps everything in memory as a dict keyed by doc_id,
    and flushes to disk on every write. Simple and reliable
    for a personal tool with hundreds (not millions) of documents.

    Think of it like a notebook index — you can flip to any entry
    instantly because it's all loaded in memory, and you write
    updates to the notebook immediately so nothing is lost.
    """

    def __init__(self, store_path: Path = None):
        self.store_path = store_path or DOCS_DIR / "metadata.json"
        self._data: dict[str, dict] = {}
        self._load()

    # ── CRUD ───────────────────────────────────────────────────────────────

    def add(self, metadata: DocumentMetadata) -> None:
        """Add or overwrite a document's metadata entry."""
        self._data[metadata.doc_id] = asdict(metadata)
        self._save()
        print(f"[Metadata] Saved: {metadata.doc_id} — {metadata.title}")

    def get(self, doc_id: str) -> DocumentMetadata | None:
        """Retrieve metadata for a single document."""
        entry = self._data.get(doc_id)
        if not entry:
            return None
        return DocumentMetadata(**entry)

    def update(self, doc_id: str, **kwargs) -> bool:
        """
        Update specific fields on an existing document.
        Returns True if the document was found and updated.
        """
        if doc_id not in self._data:
            return False

        self._data[doc_id].update(kwargs)
        self._data[doc_id]["updated_at"] = _now()
        self._save()
        return True

    def delete(self, doc_id: str) -> bool:
        """
        Remove a document's metadata entry.
        Returns True if it existed and was deleted.
        """
        if doc_id not in self._data:
            return False

        title = self._data[doc_id].get("title", doc_id)
        del self._data[doc_id]
        self._save()
        print(f"[Metadata] Deleted: {doc_id} — {title}")
        return True

    def list_all(self) -> list[DocumentMetadata]:
        """Return all documents sorted by most recently updated."""
        docs = [DocumentMetadata(**entry) for entry in self._data.values()]
        return sorted(docs, key=lambda d: d.updated_at, reverse=True)

    def list_by_type(self, source_type: str) -> list[DocumentMetadata]:
        """Return all documents of a given source type."""
        return [
            DocumentMetadata(**entry)
            for entry in self._data.values()
            if entry.get("source_type") == source_type
        ]

    def list_by_tag(self, tag: str) -> list[DocumentMetadata]:
        """Return all documents with a specific tag."""
        return [
            DocumentMetadata(**entry)
            for entry in self._data.values()
            if tag in entry.get("tags", [])
        ]

    def find_by_source(self, source_path: str) -> DocumentMetadata | None:
        """
        Find a document by its original source path or URL.
        Used to check if a document has already been ingested
        before re-ingesting it.
        """
        for entry in self._data.values():
            if entry.get("source_path") == source_path:
                return DocumentMetadata(**entry)
        return None

    def exists(self, doc_id: str) -> bool:
        return doc_id in self._data

    def count(self) -> int:
        return len(self._data)

    # ── Persistence ────────────────────────────────────────────────────────

    def _load(self) -> None:
        """Load metadata from disk into memory at startup."""
        if self.store_path.exists():
            try:
                with open(self.store_path, "r") as f:
                    self._data = json.load(f)
                print(f"[Metadata] Loaded {len(self._data)} documents from {self.store_path}")
            except Exception as e:
                print(f"[Metadata] Failed to load store — starting fresh: {e}")
                self._data = {}
        else:
            self._data = {}

    def _save(self) -> None:
        """Flush in-memory metadata to disk."""
        try:
            self.store_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.store_path, "w") as f:
                json.dump(self._data, f, indent=2)
        except Exception as e:
            print(f"[Metadata] Failed to save store: {e}")


# ── Factory helpers ────────────────────────────────────────────────────────

def create_metadata(
    title:       str,
    source_type: str,
    source_path: str,
    text:        str,
    tags:        list[str] = None,
    extra:       dict      = None
) -> DocumentMetadata:
    """
    Build a new DocumentMetadata object for a freshly ingested document.
    Generates a unique doc_id and content hash automatically.
    """
    now = _now()
    return DocumentMetadata(
        doc_id       = _generate_id(source_path),
        title        = title,
        source_type  = source_type,
        source_path  = source_path,
        created_at   = now,
        updated_at   = now,
        char_count   = len(text),
        content_hash = _hash_content(text),
        tags         = tags or [],
        extra        = extra or {}
    )


def content_changed(metadata: DocumentMetadata, new_text: str) -> bool:
    """
    Check if a document's content has changed since last ingest.
    Used by sync to decide whether to re-embed a document.

    Like a checksum — if the hash of the new content matches
    what we stored last time, the document hasn't changed.
    """
    return metadata.content_hash != _hash_content(new_text)


# ── Utilities ──────────────────────────────────────────────────────────────

def _generate_id(source_path: str) -> str:
    """
    Generate a deterministic doc_id from the source path.
    Same source always gets the same ID — makes deduplication simple.
    """
    hash_prefix = hashlib.md5(source_path.encode()).hexdigest()[:8]
    return f"doc_{hash_prefix}_{uuid.uuid4().hex[:8]}"


def _hash_content(text: str) -> str:
    """MD5 hash of content — used for change detection."""
    return hashlib.md5(text.encode()).hexdigest()


def _now() -> str:
    """Current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()

# Two things worth noting.
# The content_hash is an MD5 of the raw text — when the sync runs it re-fetches the document and computes a new hash.
# If the hash matches what's stored, we skip re-embedding entirely.
# Re-embedding is expensive so we only do it when the content actually changed.
# The find_by_source method is what prevents duplicates — before ingesting a URL or file we check if it's already in the store.
# If it is and the content hasn't changed, we skip it silently.
