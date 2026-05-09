# Token Usage & Cost Analysis

## Current Chat vs Phoenix KB Agent

### This Conversation
Every single message sends the **entire conversation history** to Claude again from the top. Currently ~100,000+ tokens per exchange.

- **Cost**: Expensive (handled by Anthropic)
- **Speed**: Fast (massive infrastructure)
- **Visibility**: Hidden from user

### Phoenix KB Agent (`/ask` endpoint)
When you hit `/ask`, it sends:

| Component | Tokens | Source |
|-----------|--------|---------|
| System prompt | ~300 | Local |
| Retrieved chunks | ~1,500 | ChromaDB |
| Conversation history | ~500 | Last 10 turns only |
| Your question | ~20 | User input |
| **Total per call** | **~2,300** | **Claude API** |

## Why Phoenix KB is More Efficient

The agent is **WAY cheaper** because:

- ✅ Only sends relevant retrieved chunks, not every document
- ✅ Caps conversation history at last 10 turns
- ✅ ChromaDB handles search locally (zero tokens)
- ✅ Only calls Claude for reasoning/generation

## Token Cost Breakdown

| Operation | Tokens Used | Where |
|-----------|-------------|-------|
| Embedding a chunk | 0 | Local model on CPU |
| Searching ChromaDB | 0 | Local vector math |
| Reranking results | 0 | Local CrossEncoder |
| Query expansion | ~200 | Claude API call |
| Generating answer | ~2,000 | Claude API call |

## Speed Comparison

| Platform | Processing Time | Total Time |
|----------|----------------|------------|
| This chat | ~0s | Fast (massive infrastructure) |
| Phoenix KB | ~1-2s local + 2-3s Claude | ~3-5s total |

## Understanding the "Fog"

The key distinction is between:

- **Storage** (ChromaDB, JSON files) → Zero tokens, zero API cost
- **Computation** (embedding, search, rerank) → Zero tokens, just CPU cycles
- **Reasoning** (Claude API calls) → This is where tokens get spent

## The Agent Pattern Advantage

The agent pattern is smart because it **minimizes reasoning calls** by doing as much as possible locally before ever calling Claude.

> **Bottom line**: The real cost driver isn't infrastructure — it's how much context you send to Claude. Phoenix KB optimizes this by only sending what's relevant.