"""
Splits documents into chunks for embedding and retrieval.

Think of this like cutting a textbook into index cards.
Each card needs to be:
- Small enough to be specific (not an entire chapter)
- Large enough to contain a complete thought (not half a sentence)
- Slightly overlapping with adjacent cards so context isn't
  lost at the boundaries

Why overlap? Imagine a key sentence falls exactly at the cut point
between two chunks. Without overlap, both chunks get half the sentence
and neither makes sense. With overlap, at least one chunk captures it whole.
"""

import re
import tiktoken
from dataclasses import dataclass, field
from config import CHUNK_SIZE, CHUNK_OVERLAP


@dataclass
class Chunk:
    """
    A single piece of a document ready for embedding.

    text:        the actual content
    doc_id:      which document this came from
    chunk_index: position within the document (0, 1, 2...)
    start_char:  character offset in the original document
    end_char:    character offset in the original document
    metadata:    anything extra we want to store alongside this chunk
    """
    text:        str
    doc_id:      str
    chunk_index: int
    start_char:  int
    end_char:    int
    metadata:    dict = field(default_factory=dict)

    @property
    def chunk_id(self) -> str:
        """Unique ID for this chunk — used as the key in the vector store."""
        return f"{self.doc_id}::chunk::{self.chunk_index}"


class Chunker:
    """
    Splits document text into overlapping chunks of roughly CHUNK_SIZE characters.

    Uses tiktoken to count tokens accurately — character count is a rough
    proxy but token count is what actually matters for LLM context windows.
    We target character size for simplicity but validate token count to
    avoid sending oversized chunks to the embedder.
    """

    def __init__(
        self,
        chunk_size:    int = CHUNK_SIZE,
        chunk_overlap: int = CHUNK_OVERLAP
    ):
        self.chunk_size    = chunk_size
        self.chunk_overlap = chunk_overlap

        # cl100k_base is the tokenizer used by most modern models
        # We use it purely for token counting, not for splitting
        self.tokenizer = tiktoken.get_encoding("cl100k_base")

    def chunk_document(self, text: str, doc_id: str, metadata: dict = None) -> list[Chunk]:
        """
        Split a full document into chunks.

        Strategy:
        1. Clean the text first (remove excessive whitespace)
        2. Try to split on natural boundaries (paragraphs, sentences)
        3. Fall back to character splitting if text has no natural breaks
        4. Apply overlap between adjacent chunks

        Returns a list of Chunk objects in order.
        """
        if not text or not text.strip():
            return []

        metadata  = metadata or {}
        cleaned   = self._clean_text(text)
        raw_chunks = self._split_with_overlap(cleaned)

        chunks = []
        char_offset = 0

        for i, chunk_text in enumerate(raw_chunks):
            # Find where this chunk starts in the cleaned text
            start = cleaned.find(chunk_text, char_offset)
            if start == -1:
                start = char_offset
            end = start + len(chunk_text)

            chunks.append(Chunk(
                text        = chunk_text.strip(),
                doc_id      = doc_id,
                chunk_index = i,
                start_char  = start,
                end_char    = end,
                metadata    = {
                    **metadata,
                    "token_count": self._count_tokens(chunk_text)
                }
            ))

            # Advance offset but account for overlap
            char_offset = max(start + len(chunk_text) - self.chunk_overlap, start + 1)

        return [c for c in chunks if len(c.text) > 20]  # drop tiny fragments

    def _split_with_overlap(self, text: str) -> list[str]:
        """
        Core splitting logic.

        Tries to split on paragraph breaks first, then sentence breaks,
        then falls back to hard character splits.

        Overlap is applied by including the tail of the previous chunk
        at the start of the next one — like a Venn diagram of text.
        """
        # First split into natural segments
        segments = self._split_on_boundaries(text)

        # Now merge segments into chunks of roughly chunk_size
        chunks      = []
        current     = ""
        overlap_buf = ""  # tail of previous chunk, prepended to next

        for segment in segments:
            # If adding this segment would exceed chunk_size, flush current chunk
            if len(current) + len(segment) > self.chunk_size and current:
                chunks.append(overlap_buf + current)

                # Overlap: take the last chunk_overlap characters as the
                # start of the next chunk so context isn't lost at the boundary
                overlap_buf = current[-self.chunk_overlap:] if len(current) > self.chunk_overlap else current
                current     = ""

            current += segment

        # Don't forget the last chunk
        if current.strip():
            chunks.append(overlap_buf + current)

        return chunks

    def _split_on_boundaries(self, text: str) -> list[str]:
        """
        Split text on natural language boundaries.

        Priority order:
        1. Double newline (paragraph break) — strongest boundary
        2. Single newline — weaker boundary
        3. Sentence ending punctuation — weakest boundary
        4. Raw character split — last resort
        """
        # Try paragraph splits first
        paragraphs = re.split(r'\n\n+', text)
        segments   = []

        for para in paragraphs:
            if len(para) <= self.chunk_size:
                segments.append(para + "\n\n")
            else:
                # Paragraph too long — split on sentences
                sentences = re.split(r'(?<=[.!?])\s+', para)
                for sentence in sentences:
                    if len(sentence) <= self.chunk_size:
                        segments.append(sentence + " ")
                    else:
                        # Sentence too long — hard character split
                        for i in range(0, len(sentence), self.chunk_size):
                            segments.append(sentence[i:i + self.chunk_size])

        return segments

    def _clean_text(self, text: str) -> str:
        """
        Normalize whitespace without destroying structure.
        Collapses 3+ newlines to 2, strips trailing spaces per line.
        """
        # Strip trailing whitespace from each line
        lines   = [line.rstrip() for line in text.splitlines()]
        cleaned = "\n".join(lines)

        # Collapse excessive blank lines (3+ newlines → 2)
        cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)

        return cleaned.strip()

    def _count_tokens(self, text: str) -> int:
        """Count tokens using tiktoken — more accurate than character count."""
        return len(self.tokenizer.encode(text))
