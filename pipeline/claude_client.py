"""
Thin wrapper around the Anthropic API used by the agent to draft each proposal section.
Centralizing the call here means prompts.py and agent.py never touch the SDK directly.
"""
import logging
import os
import time

import anthropic

from json_utils import parse_llm_json

logger = logging.getLogger("rfp_agent.pipeline.claude_client")

MODEL = "claude-sonnet-4-6"
MAX_RETRIES = 3
# Anthropic's server-side web search tool (GA, non-beta) — see
# anthropic.types.web_search_tool_20250305_param for the exact param shape this SDK version
# expects. Used only by generate_json_with_search below, for content that must reflect current
# real-world data (e.g. market rate research) rather than the model's training data alone.
WEB_SEARCH_TOOL_TYPE = "web_search_20250305"


class ClaudeClient:
    def __init__(self, api_key: str | None = None):
        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError(
                "No Anthropic API key found. Set the ANTHROPIC_API_KEY environment "
                "variable, or run the agent with --demo to see sample output without "
                "making API calls."
            )
        self.client = anthropic.Anthropic(api_key=key)

    def generate_text(self, system: str, user: str, max_tokens: int = 1500) -> str:
        """Return plain drafted text for a section."""
        last_err = None
        for attempt in range(MAX_RETRIES):
            try:
                resp = self.client.messages.create(
                    model=MODEL,
                    max_tokens=max_tokens,
                    system=system,
                    messages=[{"role": "user", "content": user}],
                )
                return "".join(
                    block.text for block in resp.content if block.type == "text"
                ).strip()
            except Exception as e:  # noqa: BLE001 - surface after retries
                last_err = e
                time.sleep(2 * (attempt + 1))
        raise RuntimeError(f"Claude API call failed after {MAX_RETRIES} attempts: {last_err}")

    def generate_json(self, system: str, user: str, max_tokens: int = 2000) -> list | dict:
        """Return parsed JSON for sections that need structured output (timeline, matrix)."""
        raw = self.generate_text(system, user, max_tokens=max_tokens)
        return parse_llm_json(raw, "Claude")

    def generate_json_with_search(self, system: str, user: str, max_tokens: int = 2000,
                                   max_uses: int = 3) -> list | dict:
        """Same contract as generate_json, but grounds the response in live web search results
        via Anthropic's server-side web_search tool — for content that must reflect current
        real-world data (e.g. market rate research) rather than potentially stale training data.

        Falls back to the plain non-search generate_json call if the search-enabled call fails
        for any reason (an older API/SDK version that doesn't support this tool, a network
        restriction in this deployment's environment, a rate limit, or any other transient
        error) — search grounding is a nice-to-have improvement on the underlying figures here,
        never a hard dependency the caller can't proceed without."""
        try:
            last_err = None
            for attempt in range(MAX_RETRIES):
                try:
                    resp = self.client.messages.create(
                        model=MODEL,
                        max_tokens=max_tokens,
                        system=system,
                        messages=[{"role": "user", "content": user}],
                        tools=[{"type": WEB_SEARCH_TOOL_TYPE, "name": "web_search", "max_uses": max_uses}],
                    )
                    raw = "".join(
                        block.text for block in resp.content if block.type == "text"
                    ).strip()
                    if not raw:
                        raise RuntimeError("Web-search-enabled call returned no text content.")
                    return parse_llm_json(raw, "Claude (web search)")
                except Exception as e:  # noqa: BLE001 - retried, then surfaced to the outer fallback
                    last_err = e
                    time.sleep(2 * (attempt + 1))
            raise RuntimeError(f"Claude web-search call failed after {MAX_RETRIES} attempts: {last_err}")
        except Exception:
            logger.warning(
                "generate_json_with_search failed — falling back to a plain (non-search) call",
                exc_info=True,
            )
            return self.generate_json(system, user, max_tokens=max_tokens)
