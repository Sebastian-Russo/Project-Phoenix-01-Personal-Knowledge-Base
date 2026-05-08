"""
Reranks retrieved chunks by relevance to the query.

The problem with embedding-based retrieval:
Embeddings are great at finding chunks that are TOPICALLY similar
to a query — but topically similar isn't the same as actually
answering the question.

Example:
  Query: "what is my Visa credit limit?"

  Chunk A: "My Visa card has a $5,000 credit limit and 18% APR"
  Chunk B: "I use my Visa card for most online purchases"
  Chunk C: "Credit cards typically have limits between $1,000 and $10,000"

Embedding search might rank B and C above A because they contain
more of the query's words — but A is clearly the best answer.

A CrossEncoder fixes this. Unlike embeddings which encode query
and chunk SEPARATELY then compare vectors, a CrossEncoder looks
at the query and chunk TOGETHER as a pair and scores how well
the chunk answers the query.

It's slower (can't pre-compute) but much more accurate.
We use it as a second-pass filter on the top N embedding results —
the embedder casts a wide net, the reranker picks the best fish.
"""

from sentence_transformers import CrossEncoder
from config import RERANKER_MODEL, RERANK_TOP_K


class Reranker:
    """
    Uses a CrossEncoder to rerank retrieval results by relevance.

    Loaded once at startup — the model is small (~25MB) and
    inference is fast enough for real-time reranking of 20 chunks.
    """

    def __init__(self, model_name: str = RERANKER_MODEL):
        print(f"[Reranker] Loading model: {model_name}")
        self.model   = CrossEncoder(model_name)
        self.top_k   = RERANK_TOP_K
        print(f"[Reranker] Ready")

    def rerank(
        self,
        query:   str,
        results: list[dict],
        top_k:   int = None
    ) -> list[dict]:
        """
        Rerank a list of retrieval results by relevance to the query.

        Takes the results from hybrid search (already filtered and
        deduplicated) and scores each chunk against the query as a pair.

        Returns the top_k most relevant chunks in ranked order,
        with the rerank_score added to each result dict.
        """
        top_k = top_k or self.top_k

        if not results:
            return []

        if len(results) == 1:
            results[0]["rerank_score"] = 1.0
            return results

        # Build (query, chunk_text) pairs for the CrossEncoder
        # The model scores each pair independently
        pairs = [(query, result["text"]) for result in results]

        # Score all pairs in one batch call
        # Returns a list of floats — higher = more relevant
        scores = self.model.predict(pairs)

        # Attach scores to results
        for result, score in zip(results, scores):
            result["rerank_score"] = round(float(score), 4)

        # Sort by rerank score descending and return top_k
        reranked = sorted(results, key=lambda r: r["rerank_score"], reverse=True)

        print(
            f"[Reranker] Reranked {len(results)} → kept {min(top_k, len(reranked))} chunks. "
            f"Top score: {reranked[0]['rerank_score']:.3f}"
        )

        return reranked[:top_k]

    def rerank_multi_query(
        self,
        queries:  list[str],
        results:  list[dict],
        top_k:    int = None
    ) -> list[dict]:
        """
        Rerank results retrieved from multiple query variations.

        When query expansion generates 4 variations, each variation
        may retrieve different chunks. This method merges all results,
        deduplicates by chunk_id, scores each chunk against the
        ORIGINAL query (first in list), and returns the best ones.

        We score against the original query — not the variations —
        because the variations were just tools for retrieval.
        The final answer needs to address the original question.
        """
        top_k = top_k or self.top_k

        if not results:
            return []

        # Deduplicate — keep the result with the highest retrieval score
        # when the same chunk appears from multiple query variations
        seen: dict[str, dict] = {}
        for result in results:
            cid = result["chunk_id"]
            if cid not in seen or result.get("score", 0) > seen[cid].get("score", 0):
                seen[cid] = result

        unique_results = list(seen.values())
        print(f"[Reranker] Deduped {len(results)} → {len(unique_results)} unique chunks")

        # Rerank against original query (first variation)
        original_query = queries[0] if queries else ""
        return self.rerank(original_query, unique_results, top_k)

# The rerank_multi_query method is the bridge between query expansion and reranking.
# Query expansion retrieves chunks using 4 different phrasings — so you end up with
# up to 80 candidate chunks (4 queries × 20 results each) with lots of duplicates.
# This method deduplicates them down to unique chunks, then scores each one against
# the original query. The result is a small set of highly relevant chunks that would
# have been missed by either expansion or reranking alone.
