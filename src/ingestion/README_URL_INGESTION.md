# URL Ingestion Strategy

## The Problem

Most modern websites are built with React, Vue, or other JavaScript
frameworks. When a standard HTTP scraper fetches these pages, it gets
the raw HTML shell — not the rendered content. The actual article text,
recipe ingredients, or page body loads after JavaScript executes, which
a basic scraper never sees.

This is why Option 1 was dropped immediately.

---

## Option 1 — requests + BeautifulSoup (DROPPED)

The original implementation. Fetches raw HTML with the `requests`
library and parses it with BeautifulSoup.

**Pros:**
- Zero extra dependencies
- Fast for simple static sites
- Fully local, nothing leaves your machine
- Easy to understand and debug

**Cons:**
- Fails on any site built with React, Vue, Angular, or similar
- Most modern sites return an empty shell with no content
- Every site needs custom parsing logic
- Noise removal (nav, footer, ads) breaks per-site
- Requires constant maintenance as sites update their HTML structure
- Bot detection blocks it on major sites

**Why we dropped it:**
The first real URL we tried — a recipe site built on React — returned
a 500 error immediately. The scraper fetched the page but got back a
JavaScript shell with no content. This is the norm for modern websites,
not the exception. Maintaining a scraper that needs per-site fixes every
time you add a new URL is not a sustainable approach for a personal tool.

---

## Option 2 — Jina Reader API (CURRENT)

Send any URL to `https://r.jina.ai/{url}`. Jina renders the page in a
real browser on their end, extracts the main content, and returns clean
markdown. No scraping code needed on our side.

**Pros:**
- One line of code — prepend `r.jina.ai/` to any URL
- Handles JavaScript-rendered sites (React, Vue, Angular, SPAs)
- Returns clean markdown already formatted for chunking
- Fast — ~1-2 seconds per page
- No browser to install, maintain, or crash
- Free tier is generous for personal use
- Handles most major sites out of the box

**Cons:**
- External dependency — if Jina goes down, URL ingestion breaks
- Pages pass through Jina's servers — not fully private
- Rate limited on free tier
- Cannot handle pages behind a login (bank statements, utility bills)
- No control over what gets extracted if Jina misses content

**Why we chose it:**
For a personal knowledge base ingesting public web pages, the tradeoffs
are acceptable. The pages being saved are already public — they pass
through your ISP, browser, and any number of CDNs before reaching you.
The simplicity win is enormous: zero scraping maintenance, works on
virtually any public site, and the code is trivially simple.

**Toggle:**
```bash
URL_FETCH_MODE=jina
```

---

## Option 3 — Playwright (FUTURE — Phoenix 02)

Launches a real Chromium browser locally, navigates to the URL,
executes JavaScript, and returns the fully rendered HTML. The same
thing your browser does, automated.

**Pros:**
- Fully local — pages never leave your machine
- Handles JavaScript rendering natively
- Can handle authenticated pages (login required)
- Can store session cookies for sites like banks and utility companies
- Can interact with the page — click, scroll, wait for elements
- No rate limits
- Works on sites that block external scrapers like Jina
- Essential for Phoenix assistant endgame (bill aggregation, bank data)

**Cons:**
- Slow — 3-5 seconds minimum per page (launches a full browser)
- Heavy — requires Chromium download (~150MB)
- More complex code — async, timeouts, browser lifecycle management
- Still gets blocked by sophisticated bot detection (Cloudflare etc.)
- Overkill for read-only public content extraction

**Why it's coming in Phoenix 02:**
The Phoenix assistant endgame involves aggregating bills from RCN,
PGI, PPL and reading bank account balances — all of which require
authenticated browser sessions. Playwright is the right tool for that.
The URL ingester is already structured to support it: set
`URL_FETCH_MODE=playwright` and the rest of the pipeline works
identically. No other code changes needed.

**Toggle (once implemented):**
```bash
URL_FETCH_MODE=playwright
```

---

## How to Switch

The active strategy is controlled by a single environment variable in `.env`:

```bash
URL_FETCH_MODE=jina        # current default
URL_FETCH_MODE=playwright  # future — Phoenix 02
```

The ingestion pipeline, chunking, embedding, and storage are identical
regardless of which mode is active. Only the fetch step changes.

---

## Strategy Comparison

| | Option 1 (Dropped) | Option 2 — Jina (Current) | Option 3 — Playwright (Future) |
|---|---|---|---|
| Modern JS sites | ❌ Fails | ✅ Works | ✅ Works |
| Authenticated pages | ❌ No | ❌ No | ✅ Yes |
| Speed | ✅ Fast | ✅ Fast (~1-2s) | ⚠️ Slow (3-5s) |
| Privacy | ✅ Local | ⚠️ Via Jina servers | ✅ Local |
| Maintenance | ❌ High | ✅ None | ⚠️ Medium |
| Dependencies | ✅ None | ✅ None extra | ⚠️ Chromium |
| Rate limits | ✅ None | ⚠️ Free tier | ✅ None |
| Login support | ❌ No | ❌ No | ✅ Yes |
| Code complexity | ⚠️ Medium | ✅ Minimal | ⚠️ High |

---

## Phoenix Roadmap

- **Phoenix 01 (now):** Jina for all public URL ingestion
- **Phoenix 02:** Playwright for authenticated bill and bank sites,
  running alongside Jina for public pages
- **Phoenix 04:** Unified ingestion layer that routes automatically —
  public URLs go to Jina, authenticated URLs go to Playwright
