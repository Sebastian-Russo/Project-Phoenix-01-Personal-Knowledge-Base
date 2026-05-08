"""
Central configuration for Project Phoenix 01 — Personal Knowledge Base.

All environment variables, paths, and tunable parameters live here.
Nothing else in the codebase reads from .env directly — they import
from this file instead. One place to change anything.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ── Paths ──────────────────────────────────────────────────────────────────
BASE_DIR    = Path(__file__).parent
CHROMA_DIR  = BASE_DIR / "chroma_db"      # vector store lives here
DOCS_DIR    = BASE_DIR / "documents"      # local document storage
CHROMA_DIR.mkdir(exist_ok=True)
DOCS_DIR.mkdir(exist_ok=True)

# ── Anthropic ──────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANSWER_MODEL      = "claude-sonnet-4-6"       # used for generating answers
AGENT_MODEL       = "claude-sonnet-4-6"       # used for KB agent reasoning

# ── Embeddings ─────────────────────────────────────────────────────────────
# Sentence transformer model used to turn text chunks into vectors.
# all-MiniLM-L6-v2 is fast, small, and good enough for personal use.
# Swap for a larger model if retrieval quality needs improvement.
EMBEDDING_MODEL   = "all-MiniLM-L6-v2"
EMBEDDING_DIM     = 384                       # dimensions for all-MiniLM-L6-v2

# ── Reranker ───────────────────────────────────────────────────────────────
# CrossEncoder reranks retrieved chunks by relevance to the query.
# Slower than the embedder but much more accurate — runs only on top N chunks.
RERANKER_MODEL    = "cross-encoder/ms-marco-MiniLM-L-6-v2"

# ── Chunking ───────────────────────────────────────────────────────────────
# Think of chunking like cutting a book into index cards.
# Too big: cards contain too much irrelevant context.
# Too small: cards lose the surrounding meaning.
CHUNK_SIZE        = 512      # characters per chunk
CHUNK_OVERLAP     = 64       # overlap between chunks to preserve context at boundaries

# ── Retrieval ──────────────────────────────────────────────────────────────
RETRIEVAL_TOP_K   = 20       # how many chunks to fetch before reranking
RERANK_TOP_K      = 5        # how many chunks to keep after reranking
SIMILARITY_THRESHOLD = 0.3   # minimum similarity score to include a chunk

# ── Google OAuth ───────────────────────────────────────────────────────────
GOOGLE_CLIENT_ID       = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET   = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI    = os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:5000/auth/callback")
GOOGLE_TOKEN_PATH      = BASE_DIR / "google_token.json"
GOOGLE_SCOPES          = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/documents.readonly"
]

# ── Google Sync ────────────────────────────────────────────────────────────
# Toggle between manual (on-demand) and realtime (background polling) sync.
# Set in .env: GOOGLE_SYNC_MODE=manual or GOOGLE_SYNC_MODE=realtime
GOOGLE_SYNC_MODE       = os.getenv("GOOGLE_SYNC_MODE", "manual")
GOOGLE_SYNC_FOLDER_ID  = os.getenv("GOOGLE_SYNC_FOLDER_ID", "")   # Drive folder to watch
GOOGLE_SYNC_INTERVAL   = int(os.getenv("GOOGLE_SYNC_INTERVAL", "300"))  # seconds (realtime mode)

# ── Flask ──────────────────────────────────────────────────────────────────
FLASK_HOST  = os.getenv("FLASK_HOST", "0.0.0.0")
FLASK_PORT  = int(os.getenv("FLASK_PORT", "5000"))
FLASK_DEBUG = os.getenv("FLASK_DEBUG", "true").lower() == "true"

# ── Browser extension ──────────────────────────────────────────────────────
# The extension sends saved pages to this endpoint.
# In development: http://localhost:5000
# In production (AWS): your deployed API URL
EXTENSION_API_URL = os.getenv("EXTENSION_API_URL", "http://localhost:5000")

# ── Validation ─────────────────────────────────────────────────────────────
def validate():
    """
    Call at startup to catch missing config early
    rather than failing silently mid-request.
    """
    errors = []

    if not ANTHROPIC_API_KEY:
        errors.append("ANTHROPIC_API_KEY is not set")
    if not GOOGLE_CLIENT_ID:
        errors.append("GOOGLE_CLIENT_ID is not set — Google Docs sync will be unavailable")
    if not GOOGLE_CLIENT_SECRET:
        errors.append("GOOGLE_CLIENT_SECRET is not set — Google Docs sync will be unavailable")
    if GOOGLE_SYNC_MODE not in ("manual", "realtime"):
        errors.append(f"GOOGLE_SYNC_MODE must be 'manual' or 'realtime', got '{GOOGLE_SYNC_MODE}'")

    for error in errors:
        print(f"[Config] ⚠️  {error}")

    return len([e for e in errors if "ANTHROPIC_API_KEY" in e]) == 0
