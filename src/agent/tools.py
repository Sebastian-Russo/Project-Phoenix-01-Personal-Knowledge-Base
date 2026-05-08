"""
Tools available to the KB agent.

Each tool is a discrete action the agent can take to answer
a question or complete a task. The agent reasons about which
tool to use, calls it, observes the result, and decides
whether it has enough information to answer or needs another tool.

Think of tools like the buttons on a Swiss Army knife:
- The agent sees all the blades
- It picks the right one for the job
- It can use multiple blades in sequence if needed

Tools defined here:
- search_kb:         search the knowledge base
- get_document:      retrieve a specific document by ID
- list_documents:    list all documents in the KB
- ingest_url:        add a web page to the KB
- ingest_text:       add raw text to the KB
- summarize_document: summarize a specific document
- get_kb_stats:      get statistics about the KB
"""

import json
from src.storage.document_store import DocumentStore
from src.retrieval.retriever    import Retriever
from src.generation.answerer    import Answerer
from src.ingestion.ingester     import Ingester


# ── Tool definitions ───────────────────────────────────────────────────────
# These dicts are passed to Claude as tool definitions.
# Claude reads the name and description to decide when to use each tool.
# The input_schema tells Claude what parameters to send.

TOOL_DEFINITIONS = [
    {
        "name":        "search_kb",
        "description": (
            "Search the personal knowledge base for information relevant to a query. "
            "Use this to find notes, documents, articles, or any stored content. "
            "Returns the most relevant text chunks with their source titles."
        ),
        "input_schema": {
            "type":       "object",
            "properties": {
                "query": {
                    "type":        "string",
                    "description": "The search query — what you're looking for"
                },
                "doc_ids": {
                    "type":        "array",
                    "items":       {"type": "string"},
                    "description": "Optional — limit search to specific document IDs"
                }
            },
            "required": ["query"]
        }
    },
    {
        "name":        "get_document",
        "description": (
            "Retrieve metadata and a preview of a specific document by its ID. "
            "Use this when you know which document contains the answer and want "
            "to inspect it directly rather than searching."
        ),
        "input_schema": {
            "type":       "object",
            "properties": {
                "doc_id": {
                    "type":        "string",
                    "description": "The document ID to retrieve"
                }
            },
            "required": ["doc_id"]
        }
    },
    {
        "name":        "list_documents",
        "description": (
            "List all documents in the knowledge base with their titles, "
            "source types, and tags. Use this to understand what's available "
            "before searching, or when the user asks what's in their KB."
        ),
        "input_schema": {
            "type":       "object",
            "properties": {
                "source_type": {
                    "type":        "string",
                    "description": "Optional filter: 'pdf', 'url', 'text', or 'gdoc'",
                    "enum":        ["pdf", "url", "text", "gdoc"]
                },
                "tag": {
                    "type":        "string",
                    "description": "Optional filter — only list docs with this tag"
                }
            }
        }
    },
    {
        "name":        "ingest_url",
        "description": (
            "Fetch a web page and add it to the knowledge base. "
            "Use when the user wants to save a URL or when a search "
            "returns no results and a relevant URL is available."
        ),
        "input_schema": {
            "type":       "object",
            "properties": {
                "url": {
                    "type":        "string",
                    "description": "The URL to fetch and ingest"
                },
                "tags": {
                    "type":        "array",
                    "items":       {"type": "string"},
                    "description": "Optional tags to apply to the document"
                }
            },
            "required": ["url"]
        }
    },
    {
        "name":        "ingest_text",
        "description": (
            "Add a piece of text directly to the knowledge base. "
            "Use when the user provides information they want to save, "
            "or when capturing a note or summary."
        ),
        "input_schema": {
            "type":       "object",
            "properties": {
                "text": {
                    "type":        "string",
                    "description": "The text content to ingest"
                },
                "title": {
                    "type":        "string",
                    "description": "A title for the document"
                },
                "tags": {
                    "type":        "array",
                    "items":       {"type": "string"},
                    "description": "Optional tags"
                }
            },
            "required": ["text", "title"]
        }
    },
    {
        "name":        "summarize_document",
        "description": (
            "Generate a summary of a specific document. "
            "Use when the user asks for an overview of a document "
            "or when you need context about a document before searching it."
        ),
        "input_schema": {
            "type":       "object",
            "properties": {
                "doc_id": {
                    "type":        "string",
                    "description": "The document ID to summarize"
                }
            },
            "required": ["doc_id"]
        }
    },
    {
        "name":        "get_kb_stats",
        "description": (
            "Get statistics about the knowledge base — total documents, "
            "chunks, breakdown by source type, and all tags. "
            "Use when the user asks about the state of their KB."
        ),
        "input_schema": {
            "type":       "object",
            "properties": {}
        }
    }
]


class KBTools:
    """
    Implements the tool functions the agent can call.

    Each method corresponds to one tool definition above.
    The agent calls execute_tool() with the tool name and
    inputs — this class routes to the right method and
    returns a JSON-serializable result.
    """

    def __init__(
        self,
        document_store: DocumentStore,
        retriever:      Retriever,
        answerer:       Answerer,
        ingester:       Ingester
    ):
        self.store    = document_store
        self.retriever = retriever
        self.answerer  = answerer
        self.ingester  = ingester

    def execute_tool(self, tool_name: str, tool_input: dict) -> str:
        """
        Route a tool call to the right method and return
        the result as a JSON string for the agent to read.
        """
        tool_map = {
            "search_kb":          self._search_kb,
            "get_document":       self._get_document,
            "list_documents":     self._list_documents,
            "ingest_url":         self._ingest_url,
            "ingest_text":        self._ingest_text,
            "summarize_document": self._summarize_document,
            "get_kb_stats":       self._get_kb_stats
        }

        fn = tool_map.get(tool_name)
        if not fn:
            return json.dumps({"error": f"Unknown tool: {tool_name}"})

        try:
            result = fn(**tool_input)
            return json.dumps(result)
        except Exception as e:
            return json.dumps({"error": str(e)})

    # ── Tool implementations ───────────────────────────────────────────────

    def _search_kb(self, query: str, doc_ids: list[str] = None) -> dict:
        """Search the KB and return formatted results."""
        chunks = self.retriever.retrieve(
            query   = query,
            doc_ids = doc_ids
        )

        if not chunks:
            return {
                "found":   False,
                "message": "No relevant content found for this query.",
                "results": []
            }

        results = [
            {
                "source":       chunk["metadata"].get("title", "Unknown"),
                "text":         chunk["text"][:500],   # preview
                "rerank_score": chunk.get("rerank_score", 0),
                "doc_id":       chunk["metadata"].get("doc_id", "")
            }
            for chunk in chunks
        ]

        return {
            "found":   True,
            "count":   len(results),
            "results": results
        }

    def _get_document(self, doc_id: str) -> dict:
        """Retrieve a document's metadata."""
        metadata = self.store.get_document(doc_id)

        if not metadata:
            return {"found": False, "message": f"Document not found: {doc_id}"}

        import dataclasses
        return {
            "found":    True,
            "document": dataclasses.asdict(metadata)
        }

    def _list_documents(
        self,
        source_type: str = None,
        tag:         str = None
    ) -> dict:
        """List documents with optional filters."""
        if source_type:
            docs = self.store.list_by_type(source_type)
        elif tag:
            docs = self.store.list_by_tag(tag)
        else:
            docs = self.store.list_documents()

        return {
            "count":     len(docs),
            "documents": [
                {
                    "doc_id":      d.doc_id,
                    "title":       d.title,
                    "source_type": d.source_type,
                    "tags":        d.tags,
                    "updated_at":  d.updated_at,
                    "chunk_count": d.chunk_count
                }
                for d in docs
            ]
        }

    def _ingest_url(self, url: str, tags: list[str] = None) -> dict:
        """Fetch and ingest a URL."""
        metadata = self.ingester.ingest_url(url, tags=tags)
        return {
            "success": True,
            "doc_id":  metadata.doc_id,
            "title":   metadata.title,
            "chunks":  metadata.chunk_count
        }

    def _ingest_text(
        self,
        text:  str,
        title: str,
        tags:  list[str] = None
    ) -> dict:
        """Ingest raw text."""
        metadata = self.ingester.ingest_text(text, title=title, tags=tags)
        return {
            "success": True,
            "doc_id":  metadata.doc_id,
            "title":   metadata.title,
            "chunks":  metadata.chunk_count
        }

    def _summarize_document(self, doc_id: str) -> dict:
        """Summarize a document."""
        metadata = self.store.get_document(doc_id)
        if not metadata:
            return {"found": False, "message": f"Document not found: {doc_id}"}

        # Search for chunks from this document to build summary context
        chunks = self.retriever.retrieve_by_document(
            query  = "main topics and key information",
            doc_id = doc_id,
            top_k  = 5
        )

        if not chunks:
            return {"found": True, "summary": metadata.summary or "No content available"}

        context = "\n\n".join(c["text"] for c in chunks)
        summary = self.answerer.summarize_document(context, metadata.title)

        return {
            "found":   True,
            "title":   metadata.title,
            "summary": summary
        }

    def _get_kb_stats(self) -> dict:
        """Return KB statistics."""
        return self.store.stats()

# The execute_tool method is the ReAct pattern in action
#  — it's the "Act" step. The agent decides which tool to use (Reason),
# calls execute_tool with a name and inputs (Act), reads the JSON result (Observe),
# and decides what to do next. Every tool returns JSON so the agent always gets
# a consistent, readable format regardless of which tool it called.
