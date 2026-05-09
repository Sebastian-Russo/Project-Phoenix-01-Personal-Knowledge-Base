"""
Fetches and cleans web pages for ingestion into the knowledge base.

Two fetch strategies, swappable via URL_FETCH_MODE in .env:

- jina:       sends URL to r.jina.ai which renders JS and returns
              clean markdown. One line of code, handles modern sites.
              Best for public web pages.

- playwright: launches a real browser locally, executes JavaScript,
              returns the fully rendered HTML. Slower but handles
              sites requiring login, cookies, or complex interactions.
              Best for authenticated pages (bank statements, utility bills).

The fetch strategy is selected at startup — the rest of the ingestion
pipeline (cleaning, metadata extraction) works the same either way.
"""

import re
import requests
from bs4 import BeautifulSoup, Tag
from markdownify import markdownify
from urllib.parse import urlparse
from config import DOCS_DIR, URL_FETCH_MODE

# ── Config ─────────────────────────────────────────────────────────────────

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept":          "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}

REQUEST_TIMEOUT = 20

# Noise tags and patterns for HTML cleaning (used by playwright mode)
NOISE_TAGS = [
    "script", "style", "nav", "footer", "header",
    "aside", "form", "button", "iframe", "noscript",
    "svg", "figure", "figcaption", "advertisement"
]

NOISE_PATTERNS = [
    "nav", "menu", "sidebar", "footer", "header", "banner",
    "cookie", "popup", "modal", "ad", "advertisement",
    "social", "share", "comment", "related", "recommended"
]


class URLIngester:
    """
    Fetches and extracts clean text from web URLs.
    Delegates to JinaFetcher or PlaywrightFetcher based on URL_FETCH_MODE.
    """

    def __init__(self):
        self.mode = URL_FETCH_MODE
        print(f"[URLIngester] Mode: {self.mode}")

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

        print(f"[URLIngester] Fetching ({self.mode}): {url}")

        if self.mode == "jina":
            return self._ingest_jina(url)
        elif self.mode == "playwright":
            return self._ingest_playwright(url)
        else:
            raise ValueError(f"Unknown URL_FETCH_MODE: {self.mode}. Use 'jina' or 'playwright'")

    # ── Jina strategy ──────────────────────────────────────────────────────

    def _ingest_jina(self, url: str) -> dict:
        """
        Fetch via Jina Reader API — prepend r.jina.ai to any URL.

        Jina renders JavaScript on their end and returns clean markdown.
        No scraping, no HTML parsing, no noise removal needed.
        The returned content is already formatted for chunking.
        """
        jina_url = f"https://r.jina.ai/{url}"

        try:
            response = requests.get(
                jina_url,
                headers = {
                    **HEADERS,
                    "Accept": "text/markdown",   # request markdown output
                    "X-Return-Format": "markdown" # Jina-specific header
                },
                timeout = REQUEST_TIMEOUT
            )
            response.raise_for_status()
            raw = response.text

        except requests.exceptions.Timeout:
            raise TimeoutError(f"Jina request timed out after {REQUEST_TIMEOUT}s: {url}")
        except requests.exceptions.ConnectionError:
            raise ConnectionError(f"Could not connect to Jina Reader: {url}")
        except requests.exceptions.HTTPError as e:
            raise ValueError(f"Jina returned HTTP {e.response.status_code} for: {url}")

        if not raw.strip():
            raise ValueError(f"Jina returned empty content for: {url}")

        # Jina includes a header block at the top with title, URL, etc.
        # Extract title from it if present, then strip it from content
        title, text = self._parse_jina_response(raw, url)

        word_count = len(text.split())
        domain     = urlparse(url).netloc.replace("www.", "")

        print(f"[URLIngester] Jina extracted {word_count} words from {domain}")

        return {
            "text":        text,
            "title":       title,
            "source_path": url,
            "source_type": "url",
            "extra": {
                "domain":     domain,
                "word_count": word_count,
                "fetch_mode": "jina"
            }
        }

    def _parse_jina_response(self, raw: str, url: str) -> tuple[str, str]:
        """
        Jina Reader prepends a metadata block to the markdown:

        Title: Page Title Here
        URL Source: https://...
        Markdown Content:
        ... actual content ...

        Extract the title and strip the header block.
        """
        title  = urlparse(url).netloc.replace("www.", "")  # fallback
        text   = raw

        lines  = raw.splitlines()

        # Look for Jina's header block
        content_start = 0
        for i, line in enumerate(lines):
            if line.startswith("Title:"):
                title = line.replace("Title:", "").strip()
            if line.startswith("Markdown Content:") or line.strip() == "":
                if i > 2:  # only skip if we found actual headers above
                    content_start = i + 1
                    break

        if content_start > 0:
            text = "\n".join(lines[content_start:]).strip()

        # Clean up excessive blank lines
        text = re.sub(r'\n{3,}', '\n\n', text)

        return title, text

    # ── Playwright strategy ────────────────────────────────────────────────

    def _ingest_playwright(self, url: str) -> dict:
        """
        Fetch via Playwright — launches a real Chromium browser locally.

        Use this for:
        - Sites requiring login (bank statements, utility bills)
        - Sites with heavy bot detection that blocks Jina
        - Pages behind authentication cookies

        Requires: pip install playwright && playwright install chromium

        ── Placeholder ──
        Full implementation coming in Phoenix 02 (Workflow Automation)
        when we need authenticated access to bank and utility sites.
        The interface is identical to _ingest_jina() so swapping
        URL_FETCH_MODE=playwright will work without changing anything else.
        """
        # ── TODO: Phoenix 02 ───────────────────────────────────
        # from playwright.sync_api import sync_playwright
        #
        # with sync_playwright() as p:
        #     browser = p.chromium.launch(headless=True)
        #     page    = browser.new_page()
        #     page.goto(url, wait_until="networkidle")
        #     html    = page.content()
        #     browser.close()
        #
        # soup  = BeautifulSoup(html, "html.parser")
        # meta  = self._extract_meta_from_soup(soup, url)
        # text  = self._extract_text_from_soup(soup)
        # return { "text": text, "title": meta["title"], ... }
        # ───────────────────────────────────────────────────────

        raise NotImplementedError(
            "Playwright mode is not yet implemented. "
            "Set URL_FETCH_MODE=jina in .env to use Jina Reader. "
            "Playwright support is coming in Phoenix 02."
        )

    # ── Shared HTML extraction (used by playwright when implemented) ───────

    def _extract_text_from_soup(self, soup: BeautifulSoup) -> str:
        """
        Extract clean text from BeautifulSoup object.
        Shared utility for the Playwright strategy.
        """
        # Remove noise tags
        for tag in NOISE_TAGS:
            for el in soup.find_all(tag):
                el.decompose()

        # Remove noise by class/id
        for el in soup.find_all(True):
            if not isinstance(el, Tag):
                continue
            el_class = " ".join(el.get("class", [])).lower()
            el_id    = (el.get("id") or "").lower()
            combined = f"{el_class} {el_id}"
            if any(pattern in combined for pattern in NOISE_PATTERNS):
                el.decompose()

        # Find main content
        main_content = (
            soup.find("article") or
            soup.find("main")    or
            soup.find(class_=re.compile(
                r"(article|post|content|entry)[_-]?(body|text|main)?", re.I
            )) or
            soup.find("body")
        )

        if not main_content:
            return ""

        markdown = markdownify(
            str(main_content),
            heading_style = "ATX",
            bullets       = "-",
            strip         = ["a"]
        )

        return re.sub(r'\n{3,}', '\n\n', markdown).strip()

    def _extract_meta_from_soup(self, soup: BeautifulSoup, url: str) -> dict:
        """Extract metadata from BeautifulSoup object."""
        domain    = urlparse(url).netloc.replace("www.", "")
        og_title  = soup.find("meta", property="og:title")
        title_tag = soup.find("title")
        title     = (
            (og_title.get("content") if og_title else None) or
            (title_tag.get_text()    if title_tag else None) or
            domain
        )
        return {
            "title":  title.strip() if title else domain,
            "domain": domain
        }

    # ── Validation ─────────────────────────────────────────────────────────

    def _validate_url(self, url: str) -> None:
        """Basic URL validation before attempting a fetch."""
        parsed = urlparse(url)

        if parsed.scheme not in ("http", "https"):
            raise ValueError(f"URL must start with http:// or https:// — got: {url}")

        if not parsed.netloc:
            raise ValueError(f"Invalid URL — no domain found: {url}")

        blocked = ["localhost", "127.0.0.1", "0.0.0.0", "192.168.", "10.", "172."]
        if any(parsed.netloc.startswith(b) for b in blocked):
            raise ValueError(f"Private/local URLs are not allowed: {url}")
