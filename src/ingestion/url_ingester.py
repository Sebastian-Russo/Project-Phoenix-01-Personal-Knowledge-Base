"""
Fetches and cleans web pages for ingestion into the knowledge base.

The web is messy. A raw HTML page contains navigation menus,
cookie banners, ads, footers, social share buttons, and scripts
— most of which is noise we don't want in our knowledge base.

Our job is to:
1. Fetch the page
2. Strip all the noise (nav, footer, ads, scripts, styles)
3. Extract the main content
4. Convert to clean markdown for consistent chunking
5. Pull useful metadata (title, author, publish date)

Think of it like a newspaper — we want the article text,
not the masthead, ads, and crossword puzzle.
"""

import re
import requests
from bs4 import BeautifulSoup
from markdownify import markdownify
from urllib.parse import urlparse
from config import DOCS_DIR


# HTML elements that are almost always noise
NOISE_TAGS = [
    "script", "style", "nav", "footer", "header",
    "aside", "form", "button", "iframe", "noscript",
    "svg", "figure", "figcaption", "advertisement"
]

# CSS classes/IDs that suggest noise content
NOISE_PATTERNS = [
    "nav", "menu", "sidebar", "footer", "header", "banner",
    "cookie", "popup", "modal", "ad", "advertisement",
    "social", "share", "comment", "related", "recommended"
]

# Request headers — identify as a browser to avoid bot blocks
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept":          "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}

REQUEST_TIMEOUT = 15  # seconds


class URLIngester:
    """
    Fetches and extracts clean text from web URLs.
    """

    def ingest(self, url: str) -> dict:
        """
        Fetch a URL and extract clean readable text.

        Returns a dict with:
        - text:        cleaned markdown text, ready for chunking
        - title:       page title
        - source_path: the URL
        - source_type: always "url"
        - extra:       domain, description, word count
        """
        url = url.strip()
        self._validate_url(url)

        print(f"[URLIngester] Fetching: {url}")

        html  = self._fetch(url)
        soup  = BeautifulSoup(html, "html.parser")
        meta  = self._extract_meta(soup, url)
        text  = self._extract_text(soup)

        if not text.strip():
            raise ValueError(f"No readable content found at: {url}")

        word_count = len(text.split())
        print(f"[URLIngester] Extracted {word_count} words from {meta['domain']}")

        return {
            "text":        text,
            "title":       meta["title"],
            "source_path": url,
            "source_type": "url",
            "extra": {
                **meta,
                "word_count": word_count
            }
        }

    # ── Private ────────────────────────────────────────────────────────────

    def _fetch(self, url: str) -> str:
        """
        Fetch HTML from a URL with sensible timeout and error handling.
        """
        try:
            response = requests.get(
                url,
                headers = HEADERS,
                timeout = REQUEST_TIMEOUT,
                allow_redirects = True
            )
            response.raise_for_status()
            return response.text

        except requests.exceptions.Timeout:
            raise TimeoutError(f"Request timed out after {REQUEST_TIMEOUT}s: {url}")
        except requests.exceptions.ConnectionError:
            raise ConnectionError(f"Could not connect to: {url}")
        except requests.exceptions.HTTPError as e:
            raise ValueError(f"HTTP {e.response.status_code} from: {url}")

    def _extract_text(self, soup: BeautifulSoup) -> str:
        """
        Extract clean readable text from parsed HTML.

        Strategy:
        1. Remove all noise elements (nav, footer, scripts, etc.)
        2. Try to find the main content container
        3. Fall back to the body if no main container found
        4. Convert to markdown for clean structure preservation
        5. Clean up excessive whitespace
        """
        # Step 1 — remove noise tags entirely
        for tag in NOISE_TAGS:
            for el in soup.find_all(tag):
                el.decompose()

        # Remove elements whose class or id suggests they're noise
        for el in soup.find_all(True):
            el_class = " ".join(el.get("class", [])).lower()
            el_id    = (el.get("id") or "").lower()
            combined = f"{el_class} {el_id}"

            if any(pattern in combined for pattern in NOISE_PATTERNS):
                el.decompose()

        # Step 2 — find main content container
        # Try semantic HTML5 elements first, then common class names
        main_content = (
            soup.find("article") or
            soup.find("main")    or
            soup.find(class_=re.compile(r"(article|post|content|entry)[_-]?(body|text|main)?", re.I)) or
            soup.find("body")
        )

        if not main_content:
            return ""

        # Step 3 — convert to markdown
        # markdownify preserves headings, lists, and code blocks
        # which helps the chunker identify natural split points
        markdown = markdownify(
            str(main_content),
            heading_style = "ATX",    # # H1, ## H2 style
            bullets       = "-",       # use - for bullet points
            strip         = ["a"]      # strip links but keep link text
        )

        # Step 4 — clean up
        return self._clean_markdown(markdown)

    def _clean_markdown(self, text: str) -> str:
        """
        Clean common artifacts from markdownify output.
        """
        # Collapse 3+ blank lines to 2
        text = re.sub(r'\n{3,}', '\n\n', text)

        # Remove lines that are just punctuation or special chars
        lines   = text.splitlines()
        cleaned = [
            line for line in lines
            if not re.match(r'^[\s\|\-\*\_\=\#\.]{0,3}$', line)
        ]

        # Remove duplicate consecutive lines (repeated headers etc.)
        deduped = []
        prev    = None
        for line in cleaned:
            if line.strip() != prev:
                deduped.append(line)
                prev = line.strip()

        return "\n".join(deduped).strip()

    def _extract_meta(self, soup: BeautifulSoup, url: str) -> dict:
        """
        Extract page metadata from HTML meta tags.
        Falls back gracefully when tags are missing.
        """
        domain = urlparse(url).netloc.replace("www.", "")

        # Title: try og:title → <title> → domain name
        og_title   = soup.find("meta", property="og:title")
        title_tag  = soup.find("title")
        title      = (
            (og_title["content"] if og_title else None) or
            (title_tag.get_text() if title_tag else None) or
            domain
        )

        # Description: try og:description → meta description
        og_desc    = soup.find("meta", property="og:description")
        meta_desc  = soup.find("meta", attrs={"name": "description"})
        description = (
            (og_desc["content"]   if og_desc   else None) or
            (meta_desc["content"] if meta_desc else None) or
            ""
        )

        # Author
        author_tag  = soup.find("meta", attrs={"name": "author"})
        author      = author_tag["content"] if author_tag else ""

        # Published date
        date_tag    = (
            soup.find("meta", property="article:published_time") or
            soup.find("time")
        )
        published   = ""
        if date_tag:
            published = date_tag.get("content") or date_tag.get("datetime") or ""

        return {
            "title":       title.strip(),
            "domain":      domain,
            "description": description.strip(),
            "author":      author.strip(),
            "published":   published.strip()
        }

    def _validate_url(self, url: str) -> None:
        """Basic URL validation before attempting a fetch."""
        parsed = urlparse(url)

        if parsed.scheme not in ("http", "https"):
            raise ValueError(f"URL must start with http:// or https:// — got: {url}")

        if not parsed.netloc:
            raise ValueError(f"Invalid URL — no domain found: {url}")

        # Block localhost and private IPs for security
        # (the browser extension could be tricked into fetching internal resources)
        blocked = ["localhost", "127.0.0.1", "0.0.0.0", "192.168.", "10.", "172."]
        if any(parsed.netloc.startswith(b) for b in blocked):
            raise ValueError(f"Private/local URLs are not allowed: {url}")

# The content extraction strategy deserves explanation.
# We try <article> first because it's the semantic HTML5 element specifically meant for main page content
# — a well-built site puts the article body there.
# Then <main>, then class name pattern matching, then the full <body> as a last resort.
# This cascade means we get clean content on well-built sites and still get something on poorly structured ones.
# The localhost block in _validate_url is a security consideration for later
# — when the browser extension is sending URLs to the Flask API, a malicious page could theoretically inject a localhost URL to probe your internal network.
# Blocking it now costs nothing and prevents the problem.
