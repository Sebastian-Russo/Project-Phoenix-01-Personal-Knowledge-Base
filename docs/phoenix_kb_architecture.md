# Phoenix Knowledge Base Architecture

```mermaid
flowchart TD
    %% ── External inputs ──
    USER(["👤 User"])
    BROWSER(["🌐 Browser Extension"])
    GDRIVE(["📁 Google Drive"])

    %% ── Flask API ──
    API["Flask API\napp.py"]

    %% ── Ingestion layer ──
    subgraph INGEST["Ingestion Layer"]
        ING["Ingester\ncoordinator"]
        PDF["PDFIngester"]
        URL["URLIngester"]
        TXT["TextIngester"]
        GDOC["GDocsIngester\n+ OAuth 2.0"]
    end

    %% ── Sync layer ──
    subgraph SYNC["Sync Layer"]
        BASE["BaseSync\ninterface"]
        MANUAL["ManualSync\non-demand"]
        REALTIME["RealtimeSync\nAPScheduler polling"]
    end

    %% ── Processing layer ──
    subgraph PROCESS["Processing Layer"]
        CHUNK["Chunker\noverlapping windows"]
        EMBED["Embedder\nall-MiniLM-L6-v2"]
        META["MetadataStore\nJSON on disk"]
    end

    %% ── Storage layer ──
    subgraph STORE["Storage Layer"]
        DOCSTORE["DocumentStore\nsingle coordinator"]
        VECTOR["VectorStore\nChromaDB local"]
    end

    %% ── Retrieval layer ──
    subgraph RETRIEVE["Retrieval Layer"]
        RET["Retriever\norchestrator"]
        EXPAND["QueryExpander\nLLM variations"]
        HYBRID["Hybrid Search\nsemantic + keyword"]
        RERANK["Reranker\nCrossEncoder"]
    end

    %% ── Generation layer ──
    subgraph GENERATE["Generation Layer"]
        ANSWER["Answerer\ngrounded generation"]
        CLAUDE["Claude API\nclaude-sonnet-4-6"]
    end

    %% ── Agent layer ──
    subgraph AGENT["Agent Layer — ReAct"]
        KBAGENT["KBAgent\norchestrator"]
        TOOLS["KBTools\n7 tools"]
    end

    %% ── Dashboard ──
    DASH["Dashboard\ndashboard/index.html"]

    %% ── Flow: ingestion ──
    USER -->|"upload file / paste text / enter URL"| API
    BROWSER -->|"POST /ingest/url"| API
    GDRIVE -->|"OAuth + Drive API"| GDOC
    API --> ING
    ING --> PDF
    ING --> URL
    ING --> TXT
    ING --> GDOC
    GDOC --> GDRIVE

    %% ── Sync triggers ingestion ──
    BASE --> MANUAL
    BASE --> REALTIME
    REALTIME -->|"poll every N seconds"| GDOC
    MANUAL -->|"on /sync call"| GDOC
    API -->|"POST /sync"| MANUAL

    %% ── Ingestion → processing → storage ──
    PDF --> DOCSTORE
    URL --> DOCSTORE
    TXT --> DOCSTORE
    GDOC --> DOCSTORE
    DOCSTORE --> CHUNK
    CHUNK --> EMBED
    EMBED --> VECTOR
    DOCSTORE --> META

    %% ── Query flow ──
    USER -->|"POST /ask"| API
    API --> KBAGENT
    KBAGENT --> TOOLS
    TOOLS -->|"search_kb"| RET
    RET --> EXPAND
    EXPAND -->|"3 variations"| CLAUDE
    RET --> HYBRID
    HYBRID -->|"semantic"| VECTOR
    HYBRID -->|"keyword"| VECTOR
    HYBRID --> RERANK
    RERANK --> ANSWER
    ANSWER --> CLAUDE
    CLAUDE -->|"grounded answer"| KBAGENT
    KBAGENT -->|"answer + sources + tool trace"| API
    API -->|"JSON response"| USER

    %% ── Dashboard ──
    USER -->|"GET /"| DASH
    DASH -->|"REST calls"| API

    %% ── Styles ──
    classDef input     fill:#1f3a6b,stroke:#58a6ff,color:#e0e0e0
    classDef api       fill:#1c2128,stroke:#58a6ff,color:#58a6ff
    classDef ingest    fill:#1a2a1a,stroke:#3fb950,color:#e0e0e0
    classDef sync      fill:#2a1d0d,stroke:#d29922,color:#e0e0e0
    classDef process   fill:#1a1a2a,stroke:#8b5cf6,color:#e0e0e0
    classDef storage   fill:#1c2128,stroke:#30363d,color:#e0e0e0
    classDef retrieve  fill:#1a2a2a,stroke:#58a6ff,color:#e0e0e0
    classDef generate  fill:#2a1a1a,stroke:#f85149,color:#e0e0e0
    classDef agent     fill:#1f3a6b,stroke:#58a6ff,color:#e0e0e0
    classDef dashboard fill:#1c2128,stroke:#30363d,color:#8b949e
    classDef external  fill:#0d1117,stroke:#555,color:#8b949e

    class USER,BROWSER input
    class GDRIVE external
    class API api
    class ING,PDF,URL,TXT,GDOC ingest
    class BASE,MANUAL,REALTIME sync
    class CHUNK,EMBED,META process
    class DOCSTORE,VECTOR storage
    class RET,EXPAND,HYBRID,RERANK retrieve
    class ANSWER,CLAUDE generate
    class KBAGENT,TOOLS agent
    class DASH dashboard
```

## Architecture Overview

This diagram shows the complete architecture of the Phoenix Knowledge Base system, including:

- **External Inputs**: User interactions, browser extension, and Google Drive integration
- **API Layer**: Flask REST API serving as the central coordinator
- **Ingestion Layer**: Multiple ingesters for different content types
- **Sync Layer**: Manual and real-time synchronization mechanisms
- **Processing Layer**: Text chunking, embedding generation, and metadata management
- **Storage Layer**: Document storage and vector database
- **Retrieval Layer**: Hybrid search with query expansion and reranking
- **Generation Layer**: Grounded answer generation using Claude API
- **Agent Layer**: ReAct-based knowledge base agent with tools
- **Dashboard**: Web interface for system interaction
