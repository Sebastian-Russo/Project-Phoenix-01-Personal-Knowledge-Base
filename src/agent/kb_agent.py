"""
The KB agent — orchestrates retrieval and tools to answer questions.

This is the brain of the knowledge base. Instead of a fixed
retrieval pipeline (query → retrieve → answer), the agent
reasons about HOW to answer before doing anything:

- Simple factual question? → search_kb directly
- "What documents do I have about X?" → list_documents first
- "Summarize my notes on Y" → search then summarize
- "Save this URL for me" → ingest_url
- Multi-part question? → multiple search_kb calls then synthesize

This is the ReAct pattern applied to a knowledge base:
Reason about what to do → Act with a tool → Observe the result
→ Reason again → Act again → ... → Final answer

The agent loop runs until Claude either:
1. Produces a final text response (no tool call) — done
2. Hits MAX_ITERATIONS — safety cutoff to prevent infinite loops

Think of the agent as a research assistant who has access to
your personal library. They don't just keyword-search one index —
they think about your question, decide which resources to check,
read them, and synthesize an answer from what they found.
"""

from anthropic import Anthropic
from src.agent.tools  import KBTools, TOOL_DEFINITIONS
from src.storage.document_store import DocumentStore
from src.retrieval.retriever    import Retriever
from src.generation.answerer    import Answerer
from src.ingestion.ingester     import Ingester
from config import ANTHROPIC_API_KEY, AGENT_MODEL


MAX_ITERATIONS = 10   # prevent infinite tool loops

AGENT_SYSTEM_PROMPT = """You are a personal knowledge base assistant with access to tools
that let you search, retrieve, and manage the user's personal documents and notes.

Your job is to answer questions and complete tasks using the user's personal knowledge base.

How to approach questions:
1. Think about what information you need to answer well
2. Use search_kb to find relevant content — always search before answering
3. If search returns nothing useful, say so honestly rather than guessing
4. For questions about what's in the KB, use list_documents first
5. For complex questions, search multiple times with different queries
6. Always cite which documents your answer comes from

You have access to the user's personal notes, saved articles, PDFs, and Google Docs.
This is private personal information — treat it with discretion.

Never make up information. If it's not in the knowledge base, say so clearly.
"""


class KBAgent:
    """
    Agentic layer over the knowledge base.

    Runs the ReAct loop: present tools to Claude, execute tool calls,
    feed results back, repeat until Claude produces a final answer.
    """

    def __init__(
        self,
        document_store: DocumentStore,
        retriever:      Retriever,
        answerer:       Answerer,
        ingester:       Ingester
    ):
        self.client = Anthropic(api_key=ANTHROPIC_API_KEY)
        self.tools  = KBTools(
            document_store = document_store,
            retriever      = retriever,
            answerer       = answerer,
            ingester       = ingester
        )

    def chat(
        self,
        message:      str,
        conversation: list[dict] = None
    ) -> dict:
        """
        Process a user message and return an answer.

        Runs the full ReAct loop — may call multiple tools before
        producing a final answer.

        Returns:
        {
            "answer":     final answer text,
            "tool_calls": list of tools used and their results,
            "iterations": number of reasoning cycles,
            "sources":    document titles referenced
        }
        """
        conversation  = conversation or []
        messages      = self._build_messages(message, conversation)
        tool_calls    = []
        sources       = set()
        iterations    = 0

        print(f"[KBAgent] Processing: '{message[:80]}'")

        while iterations < MAX_ITERATIONS:
            iterations += 1

            # ── Ask Claude what to do next ─────────────────────
            response = self.client.messages.create(
                model      = AGENT_MODEL,
                max_tokens = 2048,
                system     = AGENT_SYSTEM_PROMPT,
                tools      = TOOL_DEFINITIONS,
                messages   = messages
            )

            # ── Check stop reason ──────────────────────────────
            if response.stop_reason == "end_turn":
                # Claude produced a final answer — extract text and return
                answer = self._extract_text(response)
                print(f"[KBAgent] Done in {iterations} iteration(s)")
                return {
                    "answer":     answer,
                    "tool_calls": tool_calls,
                    "iterations": iterations,
                    "sources":    list(sources)
                }

            if response.stop_reason != "tool_use":
                # Unexpected stop reason — return whatever text we have
                answer = self._extract_text(response) or "I was unable to process your request."
                return {
                    "answer":     answer,
                    "tool_calls": tool_calls,
                    "iterations": iterations,
                    "sources":    list(sources)
                }

            # ── Execute tool calls ─────────────────────────────
            # Claude may request multiple tools in one response
            tool_results = []

            for block in response.content:
                if block.type != "tool_use":
                    continue

                tool_name  = block.name
                tool_input = block.input

                print(f"[KBAgent] Tool call: {tool_name}({list(tool_input.keys())})")

                # Execute the tool
                result_json = self.tools.execute_tool(tool_name, tool_input)

                # Track tool calls for the response
                tool_calls.append({
                    "tool":   tool_name,
                    "input":  tool_input,
                    "result": result_json[:300]  # truncate for response
                })

                # Extract source titles from search results
                if tool_name == "search_kb":
                    import json
                    try:
                        result_data = json.loads(result_json)
                        for r in result_data.get("results", []):
                            if r.get("source"):
                                sources.add(r["source"])
                    except Exception:
                        pass

                tool_results.append({
                    "type":        "tool_result",
                    "tool_use_id": block.id,
                    "content":     result_json
                })

            # ── Feed results back to Claude ────────────────────
            # Append Claude's response and tool results to the
            # message history so Claude has full context next iteration
            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user",      "content": tool_results})

        # ── Safety cutoff ──────────────────────────────────────
        print(f"[KBAgent] Hit MAX_ITERATIONS ({MAX_ITERATIONS})")
        return {
            "answer":     "I reached the maximum number of reasoning steps. Please try a more specific question.",
            "tool_calls": tool_calls,
            "iterations": iterations,
            "sources":    list(sources)
        }

    def _build_messages(
        self,
        message:      str,
        conversation: list[dict]
    ) -> list[dict]:
        """
        Build the initial messages list from conversation history
        and the new user message.
        """
        messages = list(conversation)  # copy — don't mutate caller's list
        messages.append({"role": "user", "content": message})
        return messages

    def _extract_text(self, response) -> str:
        """Extract plain text from a Claude response."""
        for block in response.content:
            if hasattr(block, "text"):
                return block.text.strip()
        return ""

# The message history management is the key part of this loop.
# After each tool call cycle we append two things to messages:
# Claude's response (which contains the tool call requests) and the tool results.
# This gives Claude the full context of what it already tried and what it found,
# so it never repeats a tool call it already made or ignores information it already retrieved.
# The sources set accumulates document titles across all search_kb calls throughout the loop —
# so the final response knows every document that was referenced regardless of how many search iterations it took.
