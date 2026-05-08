"""
Coordinates all storage operations for a single document.

Think of this as the librarian — when a new document arrives,
the librarian:
1. Registers it in the card catalog (MetadataStore)
2. Cuts it into index cards (Chunker)
3. Converts each card into a GPS coordinate (Embedder)
4. Files the cards by coordinate (VectorStore)

When a document is deleted, the librarian:
1. Removes all its index cards from the filing system (VectorStore)
2. Removes its entry from the card catalog (MetadataStore)

Nothing else in the app talks to VectorStore or MetadataStore
directly — everything goes through DocumentStore. This is the
single point of truth for what's in the knowledge base.
"""

from src.processing.chunker import Chunker
from src.processing.embedder import Embedder
from src.processing.metadata import (
    MetadataStore,
    DocumentMetadata,
    create_metadata,
    content_changed
)
from src.storage.vector_store import VectorStore


class DocumentStore:
    """
    Single interface for all document storage operations.

    Owns one instance each of Chunker, Embedder, MetadataStore,
    and VectorStore. Coordinates them to ingest, retrieve, and
    delete documents.
    """

    def __init__(self):
        self.chunker       = Chunker()
        self.embedder      = Embedder()
        self.metadata      = MetadataStore()
        self.vector_store  = VectorStore()

    # ── Ingest ─────────────────────────────────────────────────────────────

    def ingest(
        self,
        text:        str,
        title:       str,
        source_type: str,
        source_path: str,
        tags:        list[str] = None,
        extra:       dict      = None,
        force:       bool      = False
    ) -> DocumentMetadata:
        """
        Ingest a document into the knowledge base.

        Steps:
        1. Check if already ingested — skip if content unchanged
        2. Create metadata entry
        3. Chunk the text
        4. Embed the chunks
        5. Store chunks + embeddings in vector store
        6. Save metadata

        force=True skips the change detection and always re-ingests.
        Useful when you've changed chunking or embedding parameters
        and want to rebuild the index.

        Returns the DocumentMetadata for the ingested document.
        """
        if not text or not text.strip():
            raise ValueError("Cannot ingest empty document")

        # ── Deduplication check ────────────────────────────────
        existing = self.metadata.find_by_source(source_path)

        if existing and not force:
            if not content_changed(existing, text):
                print(f"[DocumentStore] Skipping unchanged document: {title}")
                return existing

            # Content changed — delete old chunks before re-ingesting
            print(f"[DocumentStore] Content changed — re-ingesting: {title}")
            self.vector_store.delete_document(existing.doc_id)
            doc_id = existing.doc_id
        else:
            doc_id = None

        # ── Build metadata ─────────────────────────────────────
        metadata = create_metadata(
            title       = title,
            source_type = source_type,
            source_path = source_path,
            text        = text,
            tags        = tags,
            extra       = extra
        )

        # Preserve original doc_id if re-ingesting
        if doc_id:
            metadata.doc_id = doc_id

        # ── Chunk ──────────────────────────────────────────────
        print(f"[DocumentStore] Chunking: {title}")
        chunks = self.chunker.chunk_document(
            text     = text,
            doc_id   = metadata.doc_id,
            metadata = {
                "title":       title,
                "source_type": source_type,
                "source_path": source_path,
                "tags":        ", ".join(tags or [])
            }
        )

        if not chunks:
            raise ValueError(f"Document produced no chunks: {title}")

        # ── Embed ──────────────────────────────────────────────
        print(f"[DocumentStore] Embedding {len(chunks)} chunks...")
        chunk_embedding_pairs = self.embedder.embed_chunks(chunks)
        chunk_list  = [pair[0] for pair in chunk_embedding_pairs]
        embed_list  = [pair[1] for pair in chunk_embedding_pairs]

        # ── Store ──────────────────────────────────────────────
        self.vector_store.add_chunks(chunk_list, embed_list)

        # ── Save metadata ──────────────────────────────────────
        metadata.chunk_count = len(chunks)
        self.metadata.add(metadata)

        print(f"[DocumentStore] ✓ Ingested: {title} — {len(chunks)} chunks")
        return metadata

    # ── Delete ─────────────────────────────────────────────────────────────

    def delete(self, doc_id: str) -> bool:
        """
        Remove a document and all its chunks from the knowledge base.
        Returns True if the document existed and was deleted.
        """
        metadata = self.metadata.get(doc_id)
        if not metadata:
            print(f"[DocumentStore] Document not found: {doc_id}")
            return False

        # Delete chunks from vector store first
        self.vector_store.delete_document(doc_id)

        # Then remove metadata entry
        self.metadata.delete(doc_id)

        print(f"[DocumentStore] ✓ Deleted: {metadata.title}")
        return True

    # ── Search ─────────────────────────────────────────────────────────────

    def search(
        self,
        query:   str,
        top_k:   int       = 20,
        doc_ids: list[str] = None
    ) -> list[dict]:
        """
        Hybrid search over all stored chunks.

        Embeds the query and runs both semantic and keyword search,
        merging and deduplicating results before returning.
        """
        query_embedding = self.embedder.embed_query(query)

        return self.vector_store.hybrid_search(
            query           = query,
            query_embedding = query_embedding,
            top_k           = top_k,
            doc_ids         = doc_ids
        )

    # ── Retrieval ──────────────────────────────────────────────────────────

    def get_document(self, doc_id: str) -> DocumentMetadata | None:
        """Retrieve metadata for a single document."""
        return self.metadata.get(doc_id)

    def list_documents(self) -> list[DocumentMetadata]:
        """List all documents sorted by most recently updated."""
        return self.metadata.list_all()

    def list_by_type(self, source_type: str) -> list[DocumentMetadata]:
        """List all documents of a given source type."""
        return self.metadata.list_by_type(source_type)

    def list_by_tag(self, tag: str) -> list[DocumentMetadata]:
        """List all documents with a specific tag."""
        return self.metadata.list_by_tag(tag)

    # ── Stats ──────────────────────────────────────────────────────────────

    def stats(self) -> dict:
        """
        Summary statistics about the knowledge base.
        Shown on the dashboard and returned by the /stats endpoint.
        """
        docs        = self.metadata.list_all()
        total_chunks = self.vector_store.count()

        by_type = {}
        for doc in docs:
            by_type[doc.source_type] = by_type.get(doc.source_type, 0) + 1

        return {
            "total_documents": len(docs),
            "total_chunks":    total_chunks,
            "by_type":         by_type,
            "total_chars":     sum(d.char_count for d in docs),
            "all_tags":        list({tag for d in docs for tag in d.tags})
        }

# The key design decision here is that DocumentStore is the only class the rest of the app ever imports for storage operations.
# Nothing else touches VectorStore, MetadataStore, Chunker, or Embedder directly.
# This means if we ever want to swap ChromaDB for a different vector store, or change the chunking strategy, we only change it in one place.
# The deduplication logic in ingest() is also worth understanding — if a document already exists and the content hash matches, we return immediately without re-embedding.
# Re-embedding is the most expensive operation in the pipeline so skipping it when nothing changed keeps the sync fast.
