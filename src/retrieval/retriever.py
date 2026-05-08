"""
Orchestrates the full retrieval pipeline.

The retrieval pipeline is the heart of the RAG system.
A query goes in, the most relevant chunks come out.

Full pipeline:
1. Expand query into multiple variations
2. Embed each variation
3. Run hybrid search for each variation
4. Merge all results
5. Rerank merged results
6. Return top K chunks

Think of it like a research assistant:
1. They rephrase your question multiple ways (expansion)
2. Search the library index for each phrasing (hybrid search)
3. Pull all the potentially relevant books (merge)
4. Read each one and decide which actually answers your question (rerank)
5. Hand you the 5 most relevant passages (top K)

The quality of the final answer depends almost entirely on
the quality of what comes out of this pipeline.
"""

from src.retrieval.query_expander import QueryExpander
from src.retrieval.reranker       import Reranker
from src.storage.document_store   import DocumentStore
from config import RETRIEVAL_TOP_K, RERANK_TOP_K


class Retriever:
    """
    Full retrieval pipeline: expand → search → rerank.

    Owns a QueryExpander and Reranker. Uses DocumentStore
    for the actual vector and keyword search.
    """

    def __init__(self, document_store: DocumentStore):
        self.store    = document_store
        self.expander = QueryExpander()
        self.reranker = Reranker()

    def retrieve(
        self,
        query:        str,
        top_k:        int        = RERANK_TOP_K,
        doc_ids:      list[str]  = None,
        expand:       bool       = True,
        conversation: list[dict] = None
    ) -> list[dict]:
        """
        Run the full retrieval pipeline for a query.

        query:        the user's question
        top_k:        number of chunks to return after reranking
        doc_ids:      optional filter — only search these documents
        expand:       whether to use query expansion (default True)
        conversation: recent chat history for context-aware expansion

        Returns a list of chunk dicts sorted by rerank score.
        Each dict has: chunk_id, text, metadata, score, rerank_score
        """
        if not query or not query.strip():
            return []

        print(f"[Retriever] Query: '{query[:80]}'")

        # ── Step 1: Query expansion ────────────────────────────
        if expand:
            if conversation:
                queries = self.expander.expand_with_context(
                    query        = query,
                    conversation = conversation
                )
            else:
                queries = self.expander.expand(query)
        else:
            queries = [query]

        # ── Step 2 + 3: Search all query variations ────────────
        all_results = self._search_all(queries, doc_ids)

        if not all_results:
            print(f"[Retriever] No results found for query")
            return []

        # ── Step 4 + 5: Rerank and return ─────────────────────
        reranked = self.reranker.rerank_multi_query(
            queries = queries,
            results = all_results,
            top_k   = top_k
        )

        print(f"[Retriever] Returning {len(reranked)} chunks")
        return reranked

    def retrieve_simple(
        self,
        query:   str,
        top_k:   int       = RERANK_TOP_K,
        doc_ids: list[str] = None
    ) -> list[dict]:
        """
        Retrieval without query expansion — faster but lower quality.

        Use when:
        - Query is already very specific and well-formed
        - Speed matters more than recall
        - Debugging retrieval quality
        """
        results = self.store.search(
            query   = query,
            top_k   = RETRIEVAL_TOP_K,
            doc_ids = doc_ids
        )

        return self.reranker.rerank(
            query   = query,
            results = results,
            top_k   = top_k
        )

    def retrieve_by_document(
        self,
        query:  str,
        doc_id: str,
        top_k:  int = RERANK_TOP_K
    ) -> list[dict]:
        """
        Retrieve chunks from a single specific document.

        Useful when the user asks "in my Q3 report, what did it say
        about revenue?" — we search only that document rather than
        the entire knowledge base.
        """
        return self.retrieve(
            query   = query,
            top_k   = top_k,
            doc_ids = [doc_id],
            expand  = True
        )

    def get_context_window(
        self,
        chunks: list[dict],
        max_chars: int = 6000
    ) -> str:
        """
        Format retrieved chunks into a context string for the LLM.

        Concatenates chunk texts with source citations so the
        generator knows where each piece of information came from.

        Respects a character budget — stops adding chunks once
        max_chars is reached to avoid overflowing the LLM context.

        Output format:
        [Source: My Notes - Meeting with John]
        chunk text here...

        [Source: Visa Statement March 2024]
        chunk text here...
        """
        context_parts = []
        total_chars   = 0

        for chunk in chunks:
            title      = chunk["metadata"].get("title", "Unknown Source")
            chunk_text = chunk["text"].strip()
            block      = f"[Source: {title}]\n{chunk_text}"

            if total_chars + len(block) > max_chars:
                # If we haven't added anything yet, include this chunk
                # truncated rather than returning empty context
                if not context_parts:
                    context_parts.append(block[:max_chars])
                break

            context_parts.append(block)
            total_chars += len(block)

        return "\n\n---\n\n".join(context_parts)

    # ── Private ────────────────────────────────────────────────────────────

    def _search_all(
        self,
        queries: list[str],
        doc_ids: list[str] = None
    ) -> list[dict]:
        """
        Run hybrid search for each query variation and collect all results.

        Each variation may surface different relevant chunks.
        Duplicates are handled by rerank_multi_query later.
        """
        all_results = []

        for query_variation in queries:
            results = self.store.search(
                query   = query_variation,
                top_k   = RETRIEVAL_TOP_K,
                doc_ids = doc_ids
            )
            all_results.extend(results)
            print(f"[Retriever] '{query_variation[:50]}' → {len(results)} results")

        return all_results

# get_context_window is the handoff point between retrieval and generation.
# It takes the reranked chunks and formats them into a single string the LLM can read,
# with [Source: title] headers before each chunk. The character budget stops us from
# overflowing the LLM's context window when chunks are long — and the "include at least
# one chunk even if it exceeds budget" fallback ensures we never send the generator an empty context.
