"""
Ingests plain text, markdown, and note files into the knowledge base.

The simplest ingester — no parsing, no HTTP calls, no PDF decoding.
Just read the file, clean it up, and hand it to the document store.

Supported formats:
- .txt  — plain text notes
- .md   — markdown files (Obsidian, Notion exports, README files)
- .rst  — reStructuredText (common in Python documentation)
- Raw string — text pasted directly via the API or dashboard

This is the ingester you'll use most for personal notes,
journal entries, meeting notes, and copied text snippets.
"""

from pathlib import Path


# File extensions this ingester handles
SUPPORTED_EXTENSIONS = {".txt", ".md", ".markdown", ".rst", ".text"}


class TextIngester:
    """
    Reads plain text and markdown files for ingestion.
    Also accepts raw strings directly — useful for the API
    endpoint where users paste text rather than uploading a file.
    """

    def ingest_file(self, file_path: str | Path) -> dict:
        """
        Read a text or markdown file and prepare it for ingestion.

        Returns a dict with:
        - text:        file contents, lightly cleaned
        - title:       filename without extension
        - source_path: absolute file path
        - source_type: always "text"
        - extra:       file size, extension, line count
        """
        path = Path(file_path).resolve()

        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")

        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            raise ValueError(
                f"Unsupported file type: {path.suffix}. "
                f"Supported: {', '.join(SUPPORTED_EXTENSIONS)}"
            )

        print(f"[TextIngester] Reading: {path.name}")

        text = self._read_file(path)

        if not text.strip():
            raise ValueError(f"File is empty: {path.name}")

        title      = self._extract_title(text, path)
        line_count = len(text.splitlines())

        print(f"[TextIngester] Read {len(text)} chars, {line_count} lines from {path.name}")

        return {
            "text":        text,
            "title":       title,
            "source_path": str(path),
            "source_type": "text",
            "extra": {
                "filename":   path.name,
                "extension":  path.suffix,
                "file_size":  path.stat().st_size,
                "line_count": line_count
            }
        }

    def ingest_string(self, text: str, title: str = None, source_id: str = None) -> dict:
        """
        Ingest a raw text string directly — no file needed.

        Used when:
        - User pastes text into the dashboard
        - API receives raw text in the request body
        - Another system sends text programmatically

        source_id is used as the source_path for deduplication.
        If not provided, a hash of the first 100 chars is used.
        """
        if not text or not text.strip():
            raise ValueError("Cannot ingest empty text")

        import hashlib
        source_id = source_id or f"text://{hashlib.md5(text[:100].encode()).hexdigest()[:12]}"
        title     = title or self._extract_title(text)

        cleaned    = self._clean_text(text)
        line_count = len(cleaned.splitlines())

        return {
            "text":        cleaned,
            "title":       title,
            "source_path": source_id,
            "source_type": "text",
            "extra": {
                "line_count": line_count
            }
        }

    # ── Private ────────────────────────────────────────────────────────────

    def _read_file(self, path: Path) -> str:
        """
        Read file with encoding detection.
        Most files are UTF-8 but we fall back to latin-1 for older files
        that were saved with Windows encoding — common with legacy notes.
        """
        try:
            return path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            try:
                return path.read_text(encoding="latin-1")
            except Exception as e:
                raise ValueError(f"Could not decode file {path.name}: {e}")

    def _extract_title(self, text: str, path: Path = None) -> str:
        """
        Extract a title from the content or filename.

        Priority:
        1. First # heading in markdown
        2. First non-empty line if it looks like a title
        3. Filename without extension
        4. "Untitled Note" as last resort
        """
        lines = [l.strip() for l in text.splitlines() if l.strip()]

        if not lines:
            return path.stem.replace("_", " ").replace("-", " ").title() if path else "Untitled Note"

        first = lines[0]

        # Markdown H1 heading
        if first.startswith("# "):
            return first[2:].strip()

        # First line looks like a title — short, no period, not a sentence
        if len(first) <= 100 and not first.endswith(".") and len(first.split()) <= 12:
            return first

        # Fall back to filename
        if path:
            return path.stem.replace("_", " ").replace("-", " ").title()

        return "Untitled Note"

    def _clean_text(self, text: str) -> str:
        """
        Light cleanup for pasted or programmatically provided text.
        Preserves structure while removing obvious noise.
        """
        import re

        # Normalize line endings (Windows \r\n → \n)
        text = text.replace("\r\n", "\n").replace("\r", "\n")

        # Collapse 3+ blank lines to 2
        text = re.sub(r'\n{3,}', '\n\n', text)

        # Strip trailing whitespace from each line
        lines = [line.rstrip() for line in text.splitlines()]

        return "\n".join(lines).strip()

# Short and clean — text ingestion genuinely is this simple.
# The interesting part is _extract_title which tries three different heuristics before giving up.
# The markdown H1 check catches Obsidian and Notion exports which always start with # Title.
# The "looks like a title" heuristic catches plain text notes that start with a subject line.
# This matters because the title shows up in citations when the KB answers a question —
# "according to your note Meeting with John - March 2024" is much more useful than
# "according to doc_a3f2b1c4".
