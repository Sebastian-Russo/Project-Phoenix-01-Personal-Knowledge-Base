"""
Generates answers from retrieved context using Claude.

This is the G in RAG — Retrieval Augmented GENERATION.
The retriever found the relevant chunks. Now we hand those
chunks to Claude and ask it to synthesize an answer.

The key constraint: Claude must answer ONLY from the provided
context — not from its training data. This is what makes the
knowledge base trustworthy. If the answer isn't in your notes,
Claude should say so rather than hallucinate something plausible.

Think of Claude here as a brilliant analyst who has only been
given specific documents to work from. They can reason, summarize,
and connect ideas across documents — but they can't use outside
knowledge. Their answer is only as good as the documents you gave them.
"""

import json
from anthropic import Anthropic
from config import ANTHROPIC_API_KEY, ANSWER_MODEL


# System prompt — defines Claude's behavior as a KB assistant
SYSTEM_PROMPT = """You are a personal knowledge base assistant. Your job is to answer
questions using ONLY the information provided in the context below.

Rules:
1. Answer ONLY from the provided context — never use outside knowledge
2. If the context doesn't contain enough information to answer, say so clearly
3. Always cite your sources using the [Source: title] format from the context
4. Be concise but complete — don't pad answers with filler
5. If multiple sources say different things, acknowledge the discrepancy
6. For personal data (finances, health, dates) be precise — don't round or approximate

If you cannot answer from the context, say:
"I don't have information about that in your knowledge base.
You may want to add a document covering this topic."
"""


class Answerer:
    """
    Generates grounded answers from retrieved context.
    Maintains conversation history for multi-turn Q&A.
    """

    def __init__(self):
        self.client = Anthropic(api_key=ANTHROPIC_API_KEY)

    def answer(
        self,
        query:        str,
        context:      str,
        conversation: list[dict] = None
    ) -> dict:
        """
        Generate an answer to a query given retrieved context.

        query:        the user's question
        context:      formatted string from Retriever.get_context_window()
        conversation: prior turns for multi-turn conversation

        Returns a dict with:
        - answer:      the generated answer text
        - sources:     list of source titles cited in the answer
        - has_answer:  whether the KB had enough info to answer
        """
        if not query.strip():
            raise ValueError("Query cannot be empty")

        messages = self._build_messages(query, context, conversation)

        response = self.client.messages.create(
            model      = ANSWER_MODEL,
            max_tokens = 1024,
            system     = SYSTEM_PROMPT,
            messages   = messages
        )

        answer_text = response.content[0].text.strip()
        sources     = self._extract_sources(context)
        has_answer  = self._check_has_answer(answer_text)

        print(
            f"[Answerer] Generated answer — "
            f"{len(answer_text)} chars, "
            f"{len(sources)} sources, "
            f"has_answer={has_answer}"
        )

        return {
            "answer":     answer_text,
            "sources":    sources,
            "has_answer": has_answer,
            "model":      ANSWER_MODEL
        }

    def answer_with_chunks(
        self,
        query:        str,
        chunks:       list[dict],
        conversation: list[dict] = None,
        max_chars:    int        = 6000
    ) -> dict:
        """
        Answer using raw chunk dicts rather than a pre-formatted context string.

        Convenience method that formats the context internally —
        useful when the caller has chunks but hasn't built the
        context string yet.
        """
        if not chunks:
            return {
                "answer":     (
                    "I don't have information about that in your knowledge base. "
                    "You may want to add a document covering this topic."
                ),
                "sources":    [],
                "has_answer": False,
                "model":      ANSWER_MODEL
            }

        # Format chunks into context string
        context_parts = []
        total_chars   = 0

        for chunk in chunks:
            title = chunk["metadata"].get("title", "Unknown Source")
            block = f"[Source: {title}]\n{chunk['text'].strip()}"

            if total_chars + len(block) > max_chars:
                if not context_parts:
                    context_parts.append(block[:max_chars])
                break

            context_parts.append(block)
            total_chars += len(block)

        context = "\n\n---\n\n".join(context_parts)
        return self.answer(query, context, conversation)

    def summarize_document(self, text: str, title: str) -> str:
        """
        Generate a one-paragraph summary of a document.

        Called during ingestion to populate the metadata summary field.
        Useful for the document list view in the dashboard —
        shows a preview without loading the full text.
        """
        prompt = (
            f"Write a concise one-paragraph summary of this document titled '{title}'. "
            f"Focus on the key topics and information it contains.\n\n"
            f"Document:\n{text[:3000]}"  # cap at 3000 chars for long docs
        )

        response = self.client.messages.create(
            model      = ANSWER_MODEL,
            max_tokens = 200,
            messages   = [{"role": "user", "content": prompt}]
        )

        return response.content[0].text.strip()

    def suggest_tags(self, text: str, title: str) -> list[str]:
        """
        Suggest relevant tags for a document based on its content.

        Called during ingestion when no tags are provided.
        Returns 3-5 lowercase tags that describe the document's topics.
        """
        prompt = (
            f"Suggest 3-5 short lowercase tags for a document titled '{title}'. "
            f"Tags should describe the main topics. "
            f"Respond with ONLY a JSON array of strings.\n\n"
            f"Document preview:\n{text[:1500]}"
        )

        response = self.client.messages.create(
            model      = ANSWER_MODEL,
            max_tokens = 100,
            messages   = [{"role": "user", "content": prompt}]
        )

        raw = response.content[0].text.strip()

        try:
            tags = json.loads(raw)
            if isinstance(tags, list):
                return [str(t).lower().strip() for t in tags if t][:5]
        except Exception:
            pass

        return []

    # ── Private ────────────────────────────────────────────────────────────

    def _build_messages(
        self,
        query:        str,
        context:      str,
        conversation: list[dict] = None
    ) -> list[dict]:
        """
        Build the messages array for the Claude API call.

        For single-turn: just one user message with context + query.
        For multi-turn: include prior conversation then new query.

        The context is injected into the first user message so
        Claude has access to it throughout the conversation.
        """
        # First message always contains the full context
        first_message = (
            f"Here is the context from my knowledge base:\n\n"
            f"{context}\n\n"
            f"---\n\n"
            f"Question: {query}"
        )

        if not conversation:
            return [{"role": "user", "content": first_message}]

        # Multi-turn: first exchange already has context embedded
        # Subsequent turns just add the new question
        messages = []

        for i, turn in enumerate(conversation):
            if i == 0 and turn["role"] == "user":
                # First user turn — inject context
                messages.append({
                    "role":    "user",
                    "content": (
                        f"Here is the context from my knowledge base:\n\n"
                        f"{context}\n\n"
                        f"---\n\n"
                        f"Question: {turn['content']}"
                    )
                })
            else:
                messages.append(turn)

        # Add the new query as the latest user turn
        messages.append({"role": "user", "content": query})
        return messages

    def _extract_sources(self, context: str) -> list[str]:
        """
        Extract source titles from the formatted context string.
        Parses [Source: title] headers to build the sources list.
        """
        import re
        pattern = r'\[Source: ([^\]]+)\]'
        matches = re.findall(pattern, context)
        # Deduplicate while preserving order
        seen    = set()
        sources = []
        for match in matches:
            if match not in seen:
                seen.add(match)
                sources.append(match)
        return sources

    def _check_has_answer(self, answer_text: str) -> bool:
        """
        Detect whether the answer indicates the KB had relevant info.
        If Claude says it doesn't have the information, flag it.
        """
        no_answer_phrases = [
            "don't have information",
            "not in your knowledge base",
            "no information about",
            "cannot find",
            "not mentioned",
            "not covered"
        ]
        lower = answer_text.lower()
        return not any(phrase in lower for phrase in no_answer_phrases)

# The suggest_tags and summarize_document methods are bonuses that pay off in the dashboard.
# When you ingest a document without specifying tags, the answerer reads the first 1500 characters
# and suggests relevant ones automatically. Over time your KB becomes self-organizing — documents
# get meaningful tags without you having to manually categorize everything.
