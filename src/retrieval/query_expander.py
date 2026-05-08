"""
Rewrites and expands queries to improve retrieval quality.

The problem with raw queries:
A user asks "what do I owe on my credit card?"
But their notes say "Visa balance is $2,400 as of March"

The words don't overlap — a keyword search finds nothing,
and semantic search might miss it if the embedding space
doesn't place "owe" and "balance" close enough together.

Query expansion fixes this by rewriting the original query
into multiple variations that cast a wider net:
- "what do I owe on my credit card?"
- "credit card balance"
- "Visa outstanding balance"
- "credit card debt amount"

We run all variations through retrieval and merge the results.
More shots at the target means more chances to hit it.

This is one of the highest-leverage improvements you can make
to a RAG system — better retrieval beats a better generator
almost every time.
"""

import json
from anthropic import Anthropic
from config import ANTHROPIC_API_KEY, ANSWER_MODEL


class QueryExpander:
    """
    Uses an LLM to rewrite queries into multiple variations
    for broader, higher-quality retrieval.
    """

    def __init__(self):
        self.client = Anthropic(api_key=ANTHROPIC_API_KEY)

    def expand(self, query: str, n_variations: int = 3) -> list[str]:
        """
        Generate n_variations alternative phrasings of the query.

        Returns a list starting with the original query followed
        by the generated variations. The original is always included
        so we never lose the user's exact phrasing.

        Example:
          Input:  "what are my monthly bills?"
          Output: [
            "what are my monthly bills?",        ← original
            "monthly recurring expenses",         ← variation 1
            "bills due each month",               ← variation 2
            "regular monthly payments and costs"  ← variation 3
          ]
        """
        if not query or not query.strip():
            return [query]

        try:
            variations = self._generate_variations(query, n_variations)
            # Always include original query first
            all_queries = [query] + [v for v in variations if v != query]
            print(f"[QueryExpander] Expanded '{query[:50]}' → {len(all_queries)} variations")
            return all_queries

        except Exception as e:
            # Never let expansion failure break the search
            # Fall back to original query silently
            print(f"[QueryExpander] Expansion failed — using original query: {e}")
            return [query]

    def expand_with_context(
        self,
        query:           str,
        conversation:    list[dict] = None,
        n_variations:    int        = 3
    ) -> list[str]:
        """
        Expand a query using conversation history as context.

        When a user asks a follow-up question like "what about the fees?"
        we need the conversation history to understand what "the fees"
        refers to. Without context, expansion would be too generic.

        conversation: list of {"role": "user"|"assistant", "content": "..."}
        """
        if not conversation:
            return self.expand(query, n_variations)

        # Build a condensed context string from recent turns
        # Only use last 3 turns to keep the prompt focused
        recent    = conversation[-6:]  # last 3 exchanges = 6 messages
        context   = "\n".join(
            f"{msg['role'].title()}: {msg['content'][:200]}"
            for msg in recent
        )

        try:
            variations = self._generate_variations_with_context(
                query, context, n_variations
            )
            all_queries = [query] + [v for v in variations if v != query]
            print(f"[QueryExpander] Context-expanded to {len(all_queries)} variations")
            return all_queries

        except Exception as e:
            print(f"[QueryExpander] Context expansion failed: {e}")
            return self.expand(query, n_variations)

    # ── Private ────────────────────────────────────────────────────────────

    def _generate_variations(self, query: str, n: int) -> list[str]:
        """
        Ask the LLM to rephrase the query in n different ways.
        Returns a list of variation strings.
        """
        prompt = f"""Generate {n} alternative phrasings of this search query.
The phrasings should capture the same intent but use different words,
synonyms, or more specific/general versions of the question.

Original query: {query}

Respond with ONLY a JSON array of strings. No explanation, no markdown.
Example format: ["variation 1", "variation 2", "variation 3"]"""

        response = self.client.messages.create(
            model      = ANSWER_MODEL,
            max_tokens = 300,
            messages   = [{"role": "user", "content": prompt}]
        )

        return self._parse_variations(response.content[0].text)

    def _generate_variations_with_context(
        self,
        query:   str,
        context: str,
        n:       int
    ) -> list[str]:
        """
        Generate variations using conversation context to resolve
        ambiguous references like "it", "that", "the fees".
        """
        prompt = f"""Given this conversation context:
{context}

Generate {n} alternative phrasings of the follow-up query: "{query}"

Resolve any ambiguous references (it, that, the, those) using the context.
Use different words and synonyms to maximize retrieval coverage.

Respond with ONLY a JSON array of strings. No explanation, no markdown.
Example format: ["variation 1", "variation 2", "variation 3"]"""

        response = self.client.messages.create(
            model      = ANSWER_MODEL,
            max_tokens = 300,
            messages   = [{"role": "user", "content": prompt}]
        )

        return self._parse_variations(response.content[0].text)

    def _parse_variations(self, raw: str) -> list[str]:
        """
        Parse LLM response into a list of strings.
        Handles cases where the model adds extra text around the JSON.
        """
        raw = raw.strip()

        # Strip markdown code fences if present
        if raw.startswith("```"):
            lines = raw.splitlines()
            raw   = "\n".join(lines[1:-1])

        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                # Filter out empty strings and non-string items
                return [str(v).strip() for v in parsed if v and str(v).strip()]
        except json.JSONDecodeError:
            pass

        # Last resort — split on newlines and clean up
        lines = [
            line.strip().strip('"').strip("'").strip("-").strip()
            for line in raw.splitlines()
            if line.strip()
        ]
        return [l for l in lines if len(l) > 5]

# The expand_with_context method is what makes follow-up questions work properly.
# When a user asks "what about the fees?" after asking about their Visa card,
# the raw query "what about the fees?" has no useful signal for retrieval.
# With context, the expander understands it means "Visa credit card fees"
# and generates variations that will actually find something.
# The silent fallback in expand() is important — if the LLM call fails for any reason,
# we return the original query and the search continues normally.
# Query expansion is an enhancement, not a requirement.
# A failed expansion should never break a search.
