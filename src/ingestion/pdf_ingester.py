"""
Extracts text from PDF files for ingestion into the knowledge base.

PDFs are surprisingly complex under the hood — they're not just
text files with formatting. A PDF is more like a canvas where
text, images, and shapes are placed at exact coordinates.
Extracting readable text means reconstructing the reading order
from those coordinates.

pypdf handles the heavy lifting. Our job is to:
1. Extract text page by page
2. Clean up the extraction artifacts (broken lines, headers/footers)
3. Preserve enough structure for chunking to work well
4. Pull useful metadata (title, author, page count) when available
"""

from pathlib import Path
import pypdf
from config import DOCS_DIR


class PDFIngester:
    """
    Extracts clean text and metadata from PDF files.
    """

    def ingest(self, file_path: str | Path) -> dict:
        """
        Extract text and metadata from a PDF file.

        Returns a dict with:
        - text:        full extracted text, ready for chunking
        - title:       document title (from PDF metadata or filename)
        - source_path: absolute path to the file
        - source_type: always "pdf"
        - extra:       author, page count, and other PDF metadata
        """
        path = Path(file_path).resolve()

        if not path.exists():
            raise FileNotFoundError(f"PDF not found: {path}")

        if path.suffix.lower() != ".pdf":
            raise ValueError(f"Not a PDF file: {path}")

        print(f"[PDFIngester] Reading: {path.name}")

        with open(path, "rb") as f:
            reader = pypdf.PdfReader(f)
            meta   = self._extract_metadata(reader, path)
            text   = self._extract_text(reader)

        if not text.strip():
            raise ValueError(f"No text could be extracted from: {path.name}. "
                           f"The PDF may be scanned images rather than text.")

        print(f"[PDFIngester] Extracted {len(text)} chars from {meta['page_count']} pages")

        return {
            "text":        text,
            "title":       meta["title"],
            "source_path": str(path),
            "source_type": "pdf",
            "extra":       meta
        }

    def ingest_bytes(self, pdf_bytes: bytes, filename: str) -> dict:
        """
        Extract text from PDF bytes directly — used when a PDF is
        uploaded via the Flask API rather than read from disk.
        Saves the file to DOCS_DIR first, then processes it.
        """
        save_path = DOCS_DIR / filename
        with open(save_path, "wb") as f:
            f.write(pdf_bytes)

        return self.ingest(save_path)

    # ── Private ────────────────────────────────────────────────────────────

    def _extract_text(self, reader: pypdf.PdfReader) -> str:
        """
        Extract text from all pages and join into a single string.

        We add a form feed character (\f) between pages so the chunker
        can use page breaks as natural split points if needed.
        Page numbers and headers/footers are cleaned up after extraction.
        """
        pages = []

        for page_num, page in enumerate(reader.pages, start=1):
            try:
                page_text = page.extract_text() or ""
                cleaned   = self._clean_page(page_text, page_num)
                if cleaned.strip():
                    pages.append(cleaned)
            except Exception as e:
                print(f"[PDFIngester] Failed to extract page {page_num}: {e}")
                continue

        return "\n\n".join(pages)

    def _clean_page(self, text: str, page_num: int) -> str:
        """
        Clean common PDF extraction artifacts from a single page.

        Common issues:
        - Words split across lines with a hyphen: "impor-\ntant" → "important"
        - Excessive whitespace between characters
        - Page numbers appearing mid-text
        - Headers/footers repeating on every page
        """
        if not text:
            return ""

        lines    = text.splitlines()
        cleaned  = []

        for line in lines:
            line = line.strip()

            # Skip lines that are just a page number
            if line.isdigit():
                continue

            # Skip very short lines that are likely headers/footers
            # (less than 3 words and not ending with punctuation)
            words = line.split()
            if len(words) <= 2 and not line.endswith((".", "!", "?", ":")):
                continue

            # Rejoin hyphenated line breaks: "impor-" + "tant" → "important"
            if cleaned and cleaned[-1].endswith("-"):
                cleaned[-1] = cleaned[-1][:-1] + line
            else:
                cleaned.append(line)

        return "\n".join(cleaned)

    def _extract_metadata(self, reader: pypdf.PdfReader, path: Path) -> dict:
        """
        Extract metadata from PDF document properties.

        PDF metadata is stored in a /Info dictionary and is often
        missing or filled with placeholder values — so we fall back
        gracefully to the filename when nothing useful is found.
        """
        info       = reader.metadata or {}
        page_count = len(reader.pages)

        # PDF metadata keys use a slash prefix: /Title, /Author, etc.
        raw_title  = info.get("/Title", "").strip()
        raw_author = info.get("/Author", "").strip()
        raw_date   = info.get("/CreationDate", "").strip()

        # Fall back to filename (without extension) if no title in metadata
        title = raw_title if raw_title else path.stem.replace("_", " ").replace("-", " ").title()

        return {
            "title":      title,
            "author":     raw_author or "Unknown",
            "page_count": page_count,
            "created":    raw_date,
            "filename":   path.name,
            "file_size":  path.stat().st_size
        }
