"""
Stores and searches document embeddings using ChromaDB.

Think of this like a library where every book has been converted
into a GPS coordinate based on its meaning. When you search,
we convert your question into its own coordinate and find all
the books whose coordinates are nearby.

ChromaDB handles the math of finding nearby vectors efficiently.
It stores everything locally in the chroma_db/ folder — no
external database, no API calls, no setup beyond pip install.

Two search modes:
- Semantic search: finds chunks with similar MEANING to the query
- Keyword search:  finds chunks containing the exact WORDS in the query

Neither alone is sufficient:
- Semantic only: misses exact matches ("what is my account number?")
- Keyword only:  misses related concepts ("power bill" vs "electricity cost")

We run both and merge the results — this is called hybrid search.
"""

import chromadb
from chromadb.config import Settings
from src.processing.chunker import Chunk
from config import CHROMA_DIR, RETRIEVAL_TOP_K, SIMILARITY_THRESHOLD


class VectorStore:
    """
    Wraps ChromaDB to provide semantic and keyword search over chunks.

    One collection per knowledge base — all documents share the same
    collection. Each chunk is stored with its embedding, text, and
    metadata so we can retrieve everything we need in one query.
    """

    def __init__(self, collection_name: str = "personal_kb"):
        self.client = chromadb.PersistentClient(
            path     = str(CHROMA_DIR),
            settings = Settings(anonymized_telemetry=False)
        )

        # Get or create the collection
        # Collections in ChromaDB are like tables — named buckets of vectors
        self.collection = self.client.get_or_create_collection(
            name     = collection_name,
            metadata = {"hnsw:space": "cosine"}  # use cosine distance for similarity
        )

        print(f"[VectorStore] Ready — {self.collection.count()} chunks indexed")

    # ── Write ──────────────────────────────────────────────────────────────

    def add_chunks(
        self,
        chunks:     list[Chunk],
        embeddings: list[list[float]]
    ) -> int:
        """
        Store chunks and their embeddings in the vector store.

        ChromaDB expects parallel lists:
        - ids:        unique string ID per chunk
        - embeddings: the vector for each chunk
        - documents:  the raw text for each chunk
        - metadatas:  a dict of metadata per chunk

        Returns number of chunks successfully added.
        """
        if not chunks or not embeddings:
            return 0

        if len(chunks) != len(embeddings):
            raise ValueError(
                f"chunks ({len(chunks)}) and embeddings ({len(embeddings)}) must be same length"
            )

        ids        = [chunk.chunk_id for chunk in chunks]
        documents  = [chunk.text for chunk in chunks]
        metadatas  = [
            {
                "doc_id":      chunk.doc_id,
                "chunk_index": chunk.chunk_index,
                "start_char":  chunk.start_char,
                "end_char":    chunk.end_char,
                **{k: str(v) for k, v in chunk.metadata.items()}
            }
            for chunk in chunks
        ]

        # ChromaDB upserts by default — if a chunk_id already exists
        # it gets overwritten. This makes re-ingestion safe.
        self.collection.upsert(
            ids        = ids,
            embeddings = embeddings,
            documents  = documents,
            metadatas  = metadatas
        )

        print(f"[VectorStore] Added {len(chunks)} chunks for doc: {chunks[0].doc_id}")
        return len(chunks)

    def delete_document(self, doc_id: str) -> int:
        """
        Delete all chunks belonging to a document.

        When a user deletes a document from the KB, we need to remove
        all its chunks from the vector store — otherwise searches will
        still return results from the deleted document.

        Returns number of chunks deleted.
        """
        results = self.collection.get(
            where = {"doc_id": doc_id}
        )

        if not results["ids"]:
            print(f"[VectorStore] No chunks found for doc: {doc_id}")
            return 0

        self.collection.delete(ids=results["ids"])
        print(f"[VectorStore] Deleted {len(results['ids'])} chunks for doc: {doc_id}")
        return len(results["ids"])

    # ── Search ─────────────────────────────────────────────────────────────

    def semantic_search(
        self,
        query_embedding: list[float],
        top_k:           int  = RETRIEVAL_TOP_K,
        doc_ids:         list[str] = None
    ) -> list[dict]:
        """
        Find chunks whose meaning is closest to the query.

        ChromaDB computes cosine distance between the query vector
        and every stored chunk vector, then returns the top_k closest.

        Optional doc_ids filter: only search within specific documents.
        Useful for "search only my notes" vs "search everything".

        Returns list of result dicts with text, metadata, and score.
        """
        where = {"doc_id": {"$in": doc_ids}} if doc_ids else None

        results = self.collection.query(
            query_embeddings = [query_embedding],
            n_results        = min(top_k, self.collection.count() or 1),
            where            = where,
            include          = ["documents", "metadatas", "distances"]
        )

        return self._format_results(results)

    def keyword_search(
        self,
        query:   str,
        top_k:   int = RETRIEVAL_TOP_K,
        doc_ids: list[str] = None
    ) -> list[dict]:
        """
        Find chunks containing the exact words from the query.

        ChromaDB supports basic full-text search via the
        $contains operator. Not as powerful as Elasticsearch
        but sufficient for personal use.

        Complements semantic search — catches exact matches that
        semantic search might miss if the embedding space doesn't
        place them close together.
        """
        where_document = {"$contains": query}
        where          = {"doc_id": {"$in": doc_ids}} if doc_ids else None

        try:
            results = self.collection.get(
                where          = where,
                where_document = where_document,
                include        = ["documents", "metadatas"],
                limit          = top_k
            )
        except Exception:
            # ChromaDB keyword search can fail on special characters
            return []

        # Keyword results don't have distance scores — assign a fixed score
        # so they can be merged with semantic results
        formatted = []
        for i, doc_id in enumerate(results.get("ids", [])):
            formatted.append({
                "chunk_id": doc_id,
                "text":     results["documents"][i],
                "metadata": results["metadatas"][i],
                "score":    0.5,         # fixed score for keyword matches
                "match_type": "keyword"
            })

        return formatted

    def hybrid_search(
        self,
        query:           str,
        query_embedding: list[float],
        top_k:           int = RETRIEVAL_TOP_K,
        doc_ids:         list[str] = None
    ) -> list[dict]:
        """
        Combine semantic and keyword search results.

        Like casting two fishing nets with different mesh sizes —
        one catches meaning, one catches exact words. We merge the
        catch, deduplicate, and sort by best score.

        Semantic results get their actual similarity score.
        Keyword-only results get a fixed score of 0.5 as a bonus
        for being an exact match.
        """
        semantic_results = self.semantic_search(query_embedding, top_k, doc_ids)
        keyword_results  = self.keyword_search(query, top_k, doc_ids)

        # Merge by chunk_id — semantic score wins if a chunk appears in both
        seen    = {}
        for result in semantic_results:
            seen[result["chunk_id"]] = result

        for result in keyword_results:
            cid = result["chunk_id"]
            if cid not in seen:
                seen[cid] = result
            else:
                # Chunk appeared in both — boost its score slightly
                seen[cid]["score"] = min(seen[cid]["score"] + 0.1, 1.0)
                seen[cid]["match_type"] = "hybrid"

        # Filter by minimum similarity threshold and sort by score
        merged = [
            r for r in seen.values()
            if r["score"] >= SIMILARITY_THRESHOLD
        ]

        return sorted(merged, key=lambda r: r["score"], reverse=True)[:top_k]

    # ── Stats ──────────────────────────────────────────────────────────────

    def count(self) -> int:
        """Total number of chunks in the store."""
        return self.collection.count()

    def count_by_doc(self, doc_id: str) -> int:
        """Number of chunks for a specific document."""
        results = self.collection.get(where={"doc_id": doc_id})
        return len(results["ids"])

    # ── Formatting ─────────────────────────────────────────────────────────

    def _format_results(self, results: dict) -> list[dict]:
        """
        Convert ChromaDB's raw query response into clean result dicts.

        ChromaDB returns distances (lower = more similar for cosine).
        We convert to similarity scores (higher = more similar) by
        computing 1 - distance, which gives us a 0-1 scale.
        """
        formatted = []

        ids       = results.get("ids", [[]])[0]
        documents = results.get("documents", [[]])[0]
        metadatas = results.get("metadatas", [[]])[0]
        distances = results.get("distances", [[]])[0]

        for chunk_id, text, metadata, distance in zip(ids, documents, metadatas, distances):
            # Convert cosine distance to similarity score
            # distance=0 means identical → score=1.0
            # distance=2 means opposite  → score=-1.0 (rare)
            score = 1 - distance

            formatted.append({
                "chunk_id":   chunk_id,
                "text":       text,
                "metadata":   metadata,
                "score":      round(score, 4),
                "match_type": "semantic"
            })

        return formatted
