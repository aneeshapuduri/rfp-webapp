"""
Thin wrapper around the Anthropic API used by the agent to draft each proposal section.
Centralizing the call here means prompts.py and agent.py never touch the SDK directly.
"""
import os
import time

import anthropic

from json_utils import parse_llm_json

MODEL = "claude-sonnet-4-6"
MAX_RETRIES = 3


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
