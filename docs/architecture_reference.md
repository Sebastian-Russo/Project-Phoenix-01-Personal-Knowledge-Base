# 🧠 Project Phoenix 01
## Personal Knowledge Base
### Study Guide & Architecture Reference

---

## What We Built

A personal knowledge base you can talk to. Dump in PDFs, paste URLs, write notes, import Google Docs — then ask questions in plain English and get answers sourced directly from your own content.

This is not a simple search engine. It is a full RAG (Retrieval Augmented Generation) system with an agent layer on top — meaning it reasons about how to answer your question before retrieving anything.

| Property | Value |
|---|---|
| Core concept | Retrieval Augmented Generation (RAG) |
| Input types | PDF, text files, URLs, Google Docs |
| Query interface | Natural language Q&A with citations |
| Agent pattern | ReAct — reason, act, observe, repeat |
| Google sync | Manual and realtime (toggled via .env) |
| Browser ext. | One-click save from any web page |
| Stack | Python, Flask, ChromaDB, Anthropic API |

---

## Core Concepts Explained

### 1. Embeddings — GPS for Meaning

An embedding is a list of numbers (a vector) that represents the meaning of a piece of text. The embedding model has learned that similar meanings produce similar vectors.

> **Analogy:** Just like two nearby cities have similar GPS coordinates, two similar sentences have similar vectors. When you search, we convert your query into a vector and find all chunks whose vectors are nearby.

- Model used: `all-MiniLM-L6-v2` (384 dimensions)
- Normalized embeddings: all vectors have length 1, making cosine similarity equivalent to dot product — faster and consistent
- Pre-trained: we never train this model, we use it as a lookup tool

---

### 2. Chunking — Index Cards for Your Documents

Documents are too long to embed as a whole. We split them into overlapping chunks that are small enough to be specific but large enough to contain a complete thought.

> **Analogy:** Like cutting a textbook into index cards. Each card needs to be small enough to be about one thing but big enough to make sense on its own.

- Chunk size: 512 characters
- Overlap: 64 characters — prevents key sentences from falling between two chunks
- Split strategy: paragraphs first, then sentences, then hard character splits
- Why overlap? If a key sentence falls at the cut point, the overlap ensures at least one chunk captures it whole

---

### 3. Hybrid Search — Two Nets Are Better Than One

We run two types of search simultaneously and merge the results:

- **Semantic search:** finds chunks with similar MEANING to the query — catches related concepts even when words differ
- **Keyword search:** finds chunks containing the EXACT words from the query — catches precise matches semantic search might miss

> **Example:** Searching "electricity bill" finds chunks about "power invoice" (semantic) AND chunks containing the literal phrase "electricity bill" (keyword).

---

### 4. Reranking — The Second Opinion

Embedding search finds topically similar chunks — but similar topic doesn't mean it actually answers the question. The CrossEncoder reranker reads query and chunk together as a pair and scores how well the chunk answers the query.

> **Analogy:** The embedder casts a wide fishing net (20 chunks). The reranker reads each fish and picks the 5 that actually answer your question.

- Model: `cross-encoder/ms-marco-MiniLM-L-6-v2`
- Runs only on top N chunks — too slow for the full vector store
- Much more accurate than embedding similarity alone

---

### 5. Query Expansion — More Shots at the Target

The user's raw query might not match the words in their notes. We use Claude to rephrase the query into 3 variations, run all 4 through retrieval, merge and deduplicate, then rerank against the original query.

> **Example:** "what do I owe on my credit card?" expands to "credit card balance", "Visa outstanding amount", "credit card debt total" — any of which might match the user's notes.

- Always includes original query first
- Falls back silently if expansion fails — never breaks search
- Context-aware: uses conversation history to resolve "it", "that", "those"

---

### 6. RAG — Grounded Generation

Instead of asking Claude to answer from training data (which may be outdated or hallucinated), we give it specific chunks from the user's own documents as context and instruct it to answer ONLY from that context.

> **Key insight:** The quality of the answer depends almost entirely on the quality of retrieval, not the power of the generator. Better retrieval beats a better LLM almost every time.

- Context window: up to 6,000 characters of retrieved chunks
- Source citations: every answer includes which documents it came from
- Honest fallback: if the KB doesn't have the answer, Claude says so

---

### 7. The ReAct Agent

Instead of a fixed pipeline (query → retrieve → answer), the KB agent reasons about HOW to answer before doing anything. It can call multiple tools, observe results, and decide what to do next.

> **Analogy:** A research assistant who thinks about your question, decides which resources to check, reads them, and synthesizes an answer — rather than just keyword-searching one index.

- Pattern: Reason → Act (tool call) → Observe (result) → Reason again → repeat
- Tools: `search_kb`, `get_document`, `list_documents`, `ingest_url`, `ingest_text`, `summarize_document`, `get_kb_stats`
- Safety: `MAX_ITERATIONS=10` prevents infinite loops
- Sources tracked: accumulates cited documents across all tool calls

---

### 8. Google Sync — Two Strategies, One Interface

Two sync modes share the same `BaseSync` interface so the rest of the app never needs to know which is active — it just calls `sync.sync()` and gets a `SyncResult` back.

- **Manual:** runs on demand via `/sync` endpoint or dashboard button. No background threads, no resource usage when idle.
- **Realtime:** APScheduler background thread polls Google Drive every N seconds. Uses `get_modified_since()` to fetch only changed docs — efficient even with large folders.
- **Toggle:** `GOOGLE_SYNC_MODE=manual|realtime` in `.env`

> **Key efficiency:** `get_modified_since()` asks Google "what changed since I last checked?" Most poll cycles make zero API calls because nothing changed.

---

## Architecture — Six Layers

The system is organized into six distinct layers. Each layer has one job. Nothing in a lower layer knows about a higher layer.

| Layer | Components | Responsibility |
|---|---|---|
| Ingestion | PDFIngester, URLIngester, TextIngester, GDocsIngester | Extract clean text from different source formats. All return the same dict shape. |
| Processing | Chunker, Embedder, MetadataStore | Split text into chunks, convert to vectors, track document metadata. |
| Storage | DocumentStore, VectorStore (ChromaDB) | Single coordinator for all storage. ChromaDB stores vectors locally. |
| Retrieval | QueryExpander, VectorStore hybrid search, Reranker, Retriever | Expand query, search, rerank. Retriever orchestrates the full pipeline. |
| Generation | Answerer, Claude API | Format context, generate grounded answers with citations. |
| Agent | KBAgent, KBTools | ReAct loop. Decides which tools to use and in what order. |

---
### Embedder and Chunker Transformations
**Chunker** splits raw text into chunks. Also just a transformation tool — no storage.

**Embedder** converts chunks into vectors (numbers). It doesn't store anything — it's just a transformation tool. Input: text. Output: list of floats.

So the processing layer is just transformations:
*raw text → Chunker → chunks → Embedder → vectors*
Nothing is stored yet at this point.

**Then storage happens in two places simultaneously:**
- **VectorStore (ChromaDB)** *stores the vectors + the chunk text + basic metadata.* This is what gets searched when you ask a question — *it's the searchable index*.
- **MetadataStore** *stores document-level info in a JSON file* — *title, source URL, tags, chunk count, content hash, created date.* This is what powers the document list in the dashboard.
- **DocumentStore** *is just the coordinator* that talks to both. It doesn't store anything itself — it *owns the Chunker, Embedder, VectorStore, and MetadataStore* and calls them in the right order.

**The full flow:**
raw text
  → DocumentStore.ingest()
      → Chunker splits into chunks
      → Embedder converts chunks to vectors (numbers)
      → VectorStore stores (vectors + chunk text)
      → MetadataStore stores (document info)
When you search:
question
  → Embedder converts question to vector (numbers)
  → VectorStore finds nearby vectors → returns chunks
  → (MetadataStore not involved in search at all)
When you list documents in the dashboard:
  → MetadataStore only — VectorStore not involved

**So they serve completely different purposes — VectorStore is for search, MetadataStore is for document management. DocumentStore just makes sure they stay in sync.**
---

## Key Design Decisions

### DocumentStore as Single Coordinator
Nothing in the codebase touches `VectorStore`, `MetadataStore`, `Chunker`, or `Embedder` directly except `DocumentStore`. This is the single point of truth for what's in the KB.

> If you ever want to swap ChromaDB for Pinecone, or change the chunking strategy, you change it in one place. Everything else continues working.

### Ingester as Mail Room
All ingestion goes through `Ingester`, which routes to the right sub-ingester and hands the result to `DocumentStore`. The caller never needs to know whether they're ingesting a PDF or a Google Doc.

### Content Hashing for Deduplication
Every document gets an MD5 hash of its content at ingest time. On re-ingest, the new hash is compared to the stored one. If they match, the document is skipped — no re-embedding, no wasted API calls.

> Re-embedding is the most expensive operation in the pipeline. Skipping unchanged documents keeps sync fast even with large document collections.

### Sync Lock Prevents Race Conditions
`RealtimeSync` uses a `threading.Lock` with `blocking=False`. If a poll cycle is still running when the next one is scheduled, the new cycle skips rather than running simultaneously. Two sync cycles writing to the same vector store simultaneously would corrupt data.

### Browser Extension Service Worker Pattern
The API call in the extension lives in `background.js` (the service worker), not `popup.js`. Popup scripts are destroyed when the popup closes — a fetch started there would be cancelled if the user closes the popup before it finishes. Service workers survive popup close.

---

## Key Principles Learned

**1. Retrieval quality beats generation quality**
The most impactful thing you can do for a RAG system is improve retrieval. A weak LLM with good retrieval beats a powerful LLM with poor retrieval. This is why we spent so much on hybrid search, reranking, and query expansion.

**2. Query expansion is high-leverage**
Rephrasing a query into multiple variations before retrieval is one of the cheapest, highest-impact improvements you can make. The user thinks in one vocabulary; their notes were written in another. Expansion bridges that gap.

**3. Overlap prevents context loss at chunk boundaries**
Without overlap, a sentence that falls exactly at a chunk boundary gets split — neither chunk makes sense in isolation. Overlap ensures key context is always captured in at least one complete chunk.

**4. Single coordinator pattern simplifies everything**
Having one class own all storage operations means there is exactly one place to look when something goes wrong with storage, and exactly one place to change when you want to swap a dependency.

**5. Feature flags beat branching code**
The `GOOGLE_SYNC_MODE` flag selects an entire strategy object at startup rather than scattering if/else branches throughout the codebase. Adding a third sync strategy means writing one new class and adding two lines to `app.py`.

**6. Grounded generation prevents hallucination**
Instructing Claude to answer ONLY from provided context — and to say "I don't have that information" when it can't — is what makes a KB trustworthy. An LLM that invents plausible-sounding answers from training data is worse than useless for personal data.

**7. Fail gracefully at every boundary**
Query expansion fails silently and returns the original query. One failed document ingest doesn't stop the rest of the folder. One failed poll cycle doesn't stop the scheduler. Each layer degrades independently rather than cascading.

---

## Phoenix Roadmap — How This Feeds the Assistant

This project is the memory layer of a larger Personal AI Operating System.

| Project | Role |
|---|---|
| Phoenix 01 (this) | Memory layer — stores and retrieves everything you've saved |
| Phoenix 02 | Action layer — workflow automation, OAuth integrations, web automation |
| Phoenix 03 | Coordination layer — multi-agent systems, specialist agents per domain |
| Phoenix 04 | The assistant — combine all three into a single interface that knows you, can act on your behalf, and replaces visiting dozens of sites individually |

The consistent API response shapes and module boundaries established in this project are intentional — they make the eventual combination clean without adding complexity to the individual project.

---

## Quick Reference

### Run the app
```bash
cd Project-Phoenix-01-Personal-Knowledge-Base
source venv/bin/activate
python app.py
# Open http://localhost:5000
```

### Key endpoints

| Endpoint | Purpose |
|---|---|
| `POST /ask` | Ask the KB agent a question |
| `POST /ingest/url` | Save a web page |
| `POST /ingest/file` | Upload a PDF or text file |
| `POST /ingest/text` | Save raw text |
| `POST /ingest/gdoc` | Import a Google Doc |
| `GET  /documents` | List all documents |
| `POST /sync` | Trigger Google Docs sync |
| `GET  /sync/status` | Check sync status |
| `GET  /auth/google` | Connect Google account |
| `GET  /stats` | KB statistics |

### Environment variables

| Variable | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | Required — your Anthropic API key |
| `GOOGLE_CLIENT_ID` | Required for Google sync |
| `GOOGLE_CLIENT_SECRET` | Required for Google sync |
| `GOOGLE_SYNC_MODE` | `manual` or `realtime` (default: manual) |
| `GOOGLE_SYNC_INTERVAL` | Seconds between polls (default: 300) |
| `GOOGLE_SYNC_FOLDER_ID` | Drive folder to watch |
| `FLASK_PORT` | Default: 5000 |

---

*Project Phoenix — Building toward a Personal AI Operating System*
