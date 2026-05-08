"""
Central coordinator for all ingestion types.

This is the single entry point for adding anything to the
knowledge base. The caller doesn't need to know whether
they're ingesting a PDF, URL, text file, or Google Doc —
they just call ingest() and this module figures out the rest.

Think of it like a mail room:
- Packages arrive in different formats (PDF, URL, text, Google Doc)
- The mail room identifies the format and routes to the right handler
- Everything comes out the other side in the same standard format
- The document store receives a consistent package regardless of origin
"""

from pathlib import Path
from src.ingestion.pdf_ingester  import PDFIngester
from src.ingestion.url_ingester  import URLIngester
from src.ingestion.text_ingester import TextIngester
from src.ingestion.gdocs_ingester import GDocsIngester
from src.storage.document_store  import DocumentStore
from src.processing.metadata     import DocumentMetadata


class Ingester:
    """
    Routes incoming content to the correct ingester and
    hands the result to DocumentStore for storage.

    One Ingester instance is shared across the app.
    Owns one instance of each sub-ingester and the DocumentStore.
    """

    def __init__(self, document_store: DocumentStore):
        self.store          = document_store
        self.pdf_ingester   = PDFIngester()
        self.url_ingester   = URLIngester()
        self.text_ingester  = TextIngester()
        self.gdocs_ingester = GDocsIngester()

    # ── Public API ─────────────────────────────────────────────────────────

    def ingest_file(
        self,
        file_path: str | Path,
        tags:      list[str] = None,
        force:     bool      = False
    ) -> DocumentMetadata:
        """
        Ingest a file from disk.
        Routes to PDF or text ingester based on file extension.
        """
        path = Path(file_path)

        if path.suffix.lower() == ".pdf":
            raw = self.pdf_ingester.ingest(file_path)
        elif path.suffix.lower() in {".txt", ".md", ".markdown", ".rst", ".text"}:
            raw = self.text_ingester.ingest_file(file_path)
        else:
            raise ValueError(
                f"Unsupported file type: {path.suffix}. "
                f"Supported: .pdf, .txt, .md, .markdown, .rst"
            )

        return self._store(raw, tags=tags, force=force)

    def ingest_pdf_bytes(
        self,
        pdf_bytes: bytes,
        filename:  str,
        tags:      list[str] = None,
        force:     bool      = False
    ) -> DocumentMetadata:
        """
        Ingest a PDF uploaded via the API as raw bytes.
        Used by the Flask /ingest/pdf endpoint.
        """
        raw = self.pdf_ingester.ingest_bytes(pdf_bytes, filename)
        return self._store(raw, tags=tags, force=force)

    def ingest_url(
        self,
        url:   str,
        tags:  list[str] = None,
        force: bool      = False
    ) -> DocumentMetadata:
        """
        Fetch and ingest a web page by URL.
        Used by the Flask /ingest/url endpoint and browser extension.
        """
        raw = self.url_ingester.ingest(url)
        return self._store(raw, tags=tags, force=force)

    def ingest_text(
        self,
        text:      str,
        title:     str      = None,
        source_id: str      = None,
        tags:      list[str] = None,
        force:     bool      = False
    ) -> DocumentMetadata:
        """
        Ingest a raw text string directly.
        Used when the user pastes text into the dashboard or API.
        """
        raw = self.text_ingester.ingest_string(text, title=title, source_id=source_id)
        return self._store(raw, tags=tags, force=force)

    def ingest_gdoc(
        self,
        doc_id: str,
        tags:   list[str] = None,
        force:  bool      = False
    ) -> DocumentMetadata:
        """
        Fetch and ingest a Google Doc by its document ID.
        Used by the sync modules and the Flask /ingest/gdoc endpoint.
        """
        raw = self.gdocs_ingester.ingest_document(doc_id)
        return self._store(raw, tags=tags, force=force)

    def ingest_gdoc_folder(
        self,
        folder_id: str      = None,
        tags:      list[str] = None,
        force:     bool      = False
    ) -> list[DocumentMetadata]:
        """
        Ingest all Google Docs in a Drive folder.
        Used by manual sync and the initial setup flow.

        Returns list of ingested DocumentMetadata objects.
        One failure doesn't stop the rest.
        """
        files   = self.gdocs_ingester.list_folder(folder_id)
        results = []

        for file in files:
            try:
                metadata = self.ingest_gdoc(
                    doc_id = file["id"],
                    tags   = tags,
                    force  = force
                )
                results.append(metadata)
            except Exception as e:
                print(f"[Ingester] Failed to ingest {file['name']}: {e}")
                continue

        print(f"[Ingester] Folder ingest complete — {len(results)}/{len(files)} succeeded")
        return results

    def delete(self, doc_id: str) -> bool:
        """Remove a document from the knowledge base."""
        return self.store.delete(doc_id)

    def list_documents(self) -> list[DocumentMetadata]:
        """List all documents in the knowledge base."""
        return self.store.list_documents()

    def stats(self) -> dict:
        """Return KB statistics."""
        return self.store.stats()

    # ── Private ────────────────────────────────────────────────────────────

    def _store(
        self,
        raw:   dict,
        tags:  list[str] = None,
        force: bool      = False
    ) -> DocumentMetadata:
        """
        Pass a raw ingestion result to DocumentStore for chunking,
        embedding, and storage.

        All ingesters return the same dict shape:
        { text, title, source_path, source_type, extra }
        So this method works the same regardless of origin.
        """
        return self.store.ingest(
            text        = raw["text"],
            title       = raw["title"],
            source_type = raw["source_type"],
            source_path = raw["source_path"],
            tags        = tags or [],
            extra       = raw.get("extra", {}),
            force       = force
        )

# The _store method is the key design point — every ingester returns the same dict shape (text, title, source_path, source_type, extra)
# so this single method handles the handoff to DocumentStore regardless of where the content came from.
# Adding a new ingester in the future (Notion, Evernote, email) just means writing the ingester class
# and adding one ingest_X method here. Nothing else changes.
