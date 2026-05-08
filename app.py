"""
Flask API entry point for Project Phoenix 01 — Personal Knowledge Base.

Thin HTTP layer — every route delegates immediately to the
appropriate module. No business logic lives here.

Routes:
  Ingestion:
    POST /ingest/file        ← upload a PDF or text file
    POST /ingest/url         ← save a web page by URL
    POST /ingest/text        ← save raw text directly
    POST /ingest/gdoc        ← save a Google Doc by ID
    POST /ingest/folder      ← sync an entire Google Drive folder

  Query:
    POST /ask                ← ask the KB agent a question
    POST /search             ← raw search without agent reasoning

  Documents:
    GET  /documents          ← list all documents
    GET  /documents/<doc_id> ← get a single document
    DELETE /documents/<doc_id> ← delete a document

  Sync:
    GET  /sync/status        ← current sync status
    POST /sync               ← trigger a manual sync

  Google Auth:
    GET  /auth/google        ← start OAuth flow
    GET  /auth/callback      ← OAuth callback

  System:
    GET  /health             ← health check
    GET  /stats              ← KB statistics
    GET  /                   ← serve dashboard
"""

import dataclasses
import json
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

import config
from config import (
    FLASK_HOST, FLASK_PORT, FLASK_DEBUG,
    GOOGLE_SYNC_MODE, GOOGLE_REDIRECT_URI
)
from src.storage.document_store import DocumentStore
from src.retrieval.retriever    import Retriever
from src.generation.answerer    import Answerer
from src.ingestion.ingester     import Ingester
from src.agent.kb_agent         import KBAgent
from src.sync.manual_sync       import ManualSync
from src.sync.realtime_sync     import RealtimeSync


# ── App setup ──────────────────────────────────────────────────────────────

app = Flask(__name__, static_folder="dashboard")
CORS(app)

# ── Shared instances ───────────────────────────────────────────────────────
# Built once at startup — expensive operations (model loading,
# DB connection) happen here, not per request.

document_store = DocumentStore()
retriever      = Retriever(document_store)
answerer       = Answerer()
ingester       = Ingester(document_store)
agent          = KBAgent(document_store, retriever, answerer, ingester)

# ── Sync setup ─────────────────────────────────────────────────────────────
# Initialize the correct sync strategy based on .env flag

if GOOGLE_SYNC_MODE == "realtime":
    sync = RealtimeSync(ingester)
else:
    sync = ManualSync(ingester)

sync.start()


# ── Dashboard ──────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory("dashboard", "index.html")


# ── Ingestion ──────────────────────────────────────────────────────────────

@app.route("/ingest/file", methods=["POST"])
def ingest_file():
    """
    Upload a PDF or text file.
    Expects multipart/form-data with a 'file' field.
    Optional: 'tags' as comma-separated string.
    """
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "Empty filename"}), 400

    tags = _parse_tags(request.form.get("tags", ""))

    try:
        # PDF upload
        if file.filename.lower().endswith(".pdf"):
            metadata = ingester.ingest_pdf_bytes(
                pdf_bytes = file.read(),
                filename  = file.filename,
                tags      = tags
            )
        else:
            # Text file — save temporarily and ingest
            from pathlib import Path
            from config import DOCS_DIR
            save_path = DOCS_DIR / file.filename
            file.save(save_path)
            metadata = ingester.ingest_file(save_path, tags=tags)

        return jsonify(_meta_to_dict(metadata))

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/ingest/url", methods=["POST"])
def ingest_url():
    """
    Ingest a web page by URL.
    Body: { "url": "https://...", "tags": ["tag1", "tag2"] }
    """
    data = request.get_json()
    url  = (data or {}).get("url", "").strip()

    if not url:
        return jsonify({"error": "url is required"}), 400

    try:
        metadata = ingester.ingest_url(url, tags=data.get("tags", []))
        return jsonify(_meta_to_dict(metadata))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/ingest/text", methods=["POST"])
def ingest_text():
    """
    Ingest raw text directly.
    Body: { "text": "...", "title": "...", "tags": [...] }
    """
    data  = request.get_json() or {}
    text  = data.get("text", "").strip()
    title = data.get("title", "").strip()

    if not text:
        return jsonify({"error": "text is required"}), 400

    try:
        metadata = ingester.ingest_text(
            text  = text,
            title = title or None,
            tags  = data.get("tags", [])
        )
        return jsonify(_meta_to_dict(metadata))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/ingest/gdoc", methods=["POST"])
def ingest_gdoc():
    """
    Ingest a Google Doc by its document ID.
    Body: { "doc_id": "...", "tags": [...] }
    """
    data   = request.get_json() or {}
    doc_id = data.get("doc_id", "").strip()

    if not doc_id:
        return jsonify({"error": "doc_id is required"}), 400

    try:
        metadata = ingester.ingest_gdoc(doc_id, tags=data.get("tags", []))
        return jsonify(_meta_to_dict(metadata))
    except PermissionError as e:
        return jsonify({"error": str(e), "auth_required": True}), 401
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/ingest/folder", methods=["POST"])
def ingest_folder():
    """
    Ingest all Google Docs in a Drive folder.
    Body: { "folder_id": "...", "tags": [...] }
    """
    data      = request.get_json() or {}
    folder_id = data.get("folder_id", "").strip() or None

    try:
        results = ingester.ingest_gdoc_folder(
            folder_id = folder_id,
            tags      = data.get("tags", [])
        )
        return jsonify({
            "ingested": len(results),
            "documents": [_meta_to_dict(m) for m in results]
        })
    except PermissionError as e:
        return jsonify({"error": str(e), "auth_required": True}), 401
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Query ──────────────────────────────────────────────────────────────────

@app.route("/ask", methods=["POST"])
def ask():
    """
    Ask the KB agent a question.
    Body: {
        "message":      "what are my monthly bills?",
        "conversation": [{"role": "user", "content": "..."}, ...]  ← optional
    }
    """
    data    = request.get_json() or {}
    message = data.get("message", "").strip()

    if not message:
        return jsonify({"error": "message is required"}), 400

    try:
        result = agent.chat(
            message      = message,
            conversation = data.get("conversation", [])
        )
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/search", methods=["POST"])
def search():
    """
    Raw search without agent reasoning — faster but less intelligent.
    Body: { "query": "...", "top_k": 5, "doc_ids": [...] }
    """
    data  = request.get_json() or {}
    query = data.get("query", "").strip()

    if not query:
        return jsonify({"error": "query is required"}), 400

    try:
        chunks = retriever.retrieve(
            query   = query,
            top_k   = data.get("top_k", 5),
            doc_ids = data.get("doc_ids")
        )
        return jsonify({
            "query":   query,
            "count":   len(chunks),
            "results": [
                {
                    "text":         c["text"],
                    "source":       c["metadata"].get("title", ""),
                    "doc_id":       c["metadata"].get("doc_id", ""),
                    "rerank_score": c.get("rerank_score", 0)
                }
                for c in chunks
            ]
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Documents ──────────────────────────────────────────────────────────────

@app.route("/documents", methods=["GET"])
def list_documents():
    """
    List all documents. Optional query params:
    ?source_type=pdf|url|text|gdoc
    ?tag=finance
    """
    source_type = request.args.get("source_type")
    tag         = request.args.get("tag")

    if source_type:
        docs = document_store.list_by_type(source_type)
    elif tag:
        docs = document_store.list_by_tag(tag)
    else:
        docs = document_store.list_documents()

    return jsonify({
        "count":     len(docs),
        "documents": [_meta_to_dict(d) for d in docs]
    })


@app.route("/documents/<doc_id>", methods=["GET"])
def get_document(doc_id):
    """Get a single document by ID."""
    metadata = document_store.get_document(doc_id)
    if not metadata:
        return jsonify({"error": f"Document not found: {doc_id}"}), 404
    return jsonify(_meta_to_dict(metadata))


@app.route("/documents/<doc_id>", methods=["DELETE"])
def delete_document(doc_id):
    """Delete a document and all its chunks."""
    deleted = ingester.delete(doc_id)
    if not deleted:
        return jsonify({"error": f"Document not found: {doc_id}"}), 404
    return jsonify({"deleted": True, "doc_id": doc_id})


# ── Sync ───────────────────────────────────────────────────────────────────

@app.route("/sync/status", methods=["GET"])
def sync_status():
    """Return current sync status."""
    return jsonify(sync.get_status())


@app.route("/sync", methods=["POST"])
def trigger_sync():
    """
    Trigger a sync run.
    Works for both manual and realtime modes.
    Body (optional): { "folder_id": "...", "tags": [...] }
    """
    data = request.get_json() or {}

    try:
        result = sync.sync(
            folder_id = data.get("folder_id"),
            tags      = data.get("tags")
        )
        return jsonify({
            "ingested":  result.ingested,
            "skipped":   result.skipped,
            "failed":    result.failed,
            "duration":  result.duration_seconds,
            "summary":   result.summary()
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Google Auth ────────────────────────────────────────────────────────────

@app.route("/auth/google", methods=["GET"])
def auth_google():
    """Start the Google OAuth flow — redirects to Google login."""
    try:
        auth_url = ingester.gdocs_ingester.authenticate()
        from flask import redirect
        return redirect(auth_url)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/auth/callback", methods=["GET"])
def auth_callback():
    """
    Handle Google OAuth callback.
    Google redirects here after the user logs in.
    """
    code = request.args.get("code")
    if not code:
        return jsonify({"error": "No authorization code received"}), 400

    try:
        ingester.gdocs_ingester.handle_callback(code)
        return """
            <html><body>
            <h2>✅ Google account connected successfully.</h2>
            <p>You can close this tab and return to the dashboard.</p>
            </body></html>
        """
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── System ─────────────────────────────────────────────────────────────────

@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status":    "ok",
        "sync_mode": GOOGLE_SYNC_MODE,
        "google":    ingester.gdocs_ingester.is_authenticated()
    })


@app.route("/stats", methods=["GET"])
def stats():
    return jsonify(document_store.stats())


# ── Helpers ────────────────────────────────────────────────────────────────

def _meta_to_dict(metadata) -> dict:
    """Convert DocumentMetadata dataclass to a JSON-serializable dict."""
    return dataclasses.asdict(metadata)


def _parse_tags(tags_str: str) -> list[str]:
    """Parse a comma-separated tags string into a list."""
    if not tags_str:
        return []
    return [t.strip() for t in tags_str.split(",") if t.strip()]


# ── Startup ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    config.validate()
    app.run(
        host  = FLASK_HOST,
        port  = FLASK_PORT,
        debug = FLASK_DEBUG
    )

# The shared instances at the top are important
# — DocumentStore, Retriever, Answerer, Ingester, and KBAgent are all built once when Flask starts.
# Building them per-request would reload the embedding model on every call which takes 3-5 seconds.
# Built once, they're reused for every request for the lifetime of the process.
# The sync strategy swap is also clean here — two lines pick either RealtimeSync or ManualSync based on the .env flag,
# then sync.start() works the same either way. The rest of the file never references which mode is active.
