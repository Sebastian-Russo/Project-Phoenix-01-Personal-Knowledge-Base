"""
Turns text chunks into vectors (embeddings) for semantic search.

Think of an embedding as a GPS coordinate for meaning.
Just like two nearby locations have similar coordinates,
two pieces of text with similar meaning have similar vectors.

When you search "what are my monthly bills?", we convert
that question into a vector, then find all chunks whose
vectors are nearby in that meaning-space. That's semantic search.

The embedding model (all-MiniLM-L6-v2) was trained on hundreds
of millions of sentence pairs to understand that:
"car" and "automobile" are close together
"car" and "sandwich" are far apart
"my electricity bill is overdue" and "what do I owe for power?" are close

We never train this model — we just use it as a lookup tool.
"""

import torch
import numpy as np
from sentence_transformers import SentenceTransformer
from src.processing.chunker import Chunk
from config import EMBEDDING_MODEL, EMBEDDING_DIM


class Embedder:
    """
    Wraps SentenceTransformer to embed chunks and queries.

    One Embedder instance is shared across the app — loading
    the model is expensive (a few seconds) so we do it once
    at startup and reuse it for every request.
    """

    def __init__(self, model_name: str = EMBEDDING_MODEL):
        print(f"[Embedder] Loading model: {model_name}")

        # Use GPU if available, otherwise CPU
        # For a personal KB on a laptop, CPU is fine
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.model  = SentenceTransformer(model_name, device=self.device)
        self.dim    = EMBEDDING_DIM

        print(f"[Embedder] Ready on {self.device}")

    def embed_chunks(self, chunks: list[Chunk]) -> list[tuple[Chunk, list[float]]]:
        """
        Embed a list of chunks.

        Batches the embedding calls for efficiency — sending 100 chunks
        to the model at once is much faster than 100 individual calls.

        Returns a list of (chunk, embedding) pairs so the caller can
        store both together.
        """
        if not chunks:
            return []

        texts      = [chunk.text for chunk in chunks]
        embeddings = self._embed_batch(texts)

        return list(zip(chunks, embeddings))

    def embed_query(self, query: str) -> list[float]:
        """
        Embed a single search query.

        Queries are embedded the same way as chunks — same model,
        same vector space — so their vectors are directly comparable.
        This is what makes semantic search work: query vector and
        chunk vectors live in the same space, so we can measure distance.
        """
        if not query or not query.strip():
            raise ValueError("Query cannot be empty")

        embeddings = self._embed_batch([query.strip()])
        return embeddings[0]

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """
        Embed a list of raw strings.
        Used when we need embeddings without Chunk objects.
        """
        if not texts:
            return []
        return self._embed_batch(texts)

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        """
        Core embedding call.

        normalize_embeddings=True ensures all vectors have length 1
        (unit vectors). This makes cosine similarity equivalent to
        dot product — faster to compute and consistent across chunks
        of different text lengths.

        Think of normalization like converting all distances to percentages
        so you can compare them fairly regardless of original scale.
        """
        embeddings = self.model.encode(
            texts,
            batch_size          = 64,         # process 64 texts at a time
            show_progress_bar   = len(texts) > 100,  # show bar for large batches
            normalize_embeddings = True,       # unit vectors for cosine similarity
            convert_to_numpy    = True
        )

        # Convert numpy arrays to plain Python lists for JSON serialization
        # and ChromaDB compatibility
        return [embedding.tolist() for embedding in embeddings]

    def similarity(self, vec_a: list[float], vec_b: list[float]) -> float:
        """
        Compute cosine similarity between two vectors.

        Returns a value between -1 and 1:
          1.0  = identical meaning
          0.0  = unrelated
         -1.0  = opposite meaning (rare in practice)

        Since we normalize embeddings, this is just a dot product.
        """
        a = np.array(vec_a)
        b = np.array(vec_b)
        return float(np.dot(a, b))

# The key insight here is normalization.
# By forcing every vector to have length 1, cosine similarity becomes a simple dot product — mathematically cheaper and consistent regardless of how long the original text was.
# A 10-word chunk and a 500-word chunk both get unit vectors, so their similarity scores are directly comparable.
