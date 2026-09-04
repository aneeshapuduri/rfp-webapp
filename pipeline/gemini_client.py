"""
Thin wrapper around the Google Gemini API, matching the exact same interface as
claude_client.ClaudeClient (generate_text / generate_json) so the rest of the pipeline never
needs to know which provider is actually running underneath it.
"""
import os
import random
import re
import time

from google import genai
from google.genai import types

from json_utils import parse_llm_json

MODEL = "gemini-3.1-flash-lite"
MAX_RETRIES = 4
MAX_BACKOFF_SECONDS = 45

# Google's 429 response body embeds its own suggested wait, e.g. "'retryDelay': '22s'" — when
# present this is honored instead of guessing, since the free-tier per-minute quota window is
# longer than a short fixed backoff can cover.
_RETRY_DELAY_RE = re.compile(r"retryDelay['\"]?\s*:\s*['\"]?(\d+(?:\.\d+)?)s")


def _is_rate_limited(exc: Exception) -> bool:
    text = str(exc)
    return "RESOURCE_EXHAUSTED" in text or "429" in text


def _suggested_retry_delay(exc: Exception) -> float | None:
    match = _RETRY_DELAY_RE.search(str(exc))
    return float(match.group(1)) if match else None


def _backoff_seconds(attempt: int, exc: Exception) -> float:
    """A 429 RESOURCE_EXHAUSTED (Gemini's free-tier cap of ~15 requests/minute for this model)
    needs a backoff long enough to actually clear the per-minute window — the old fixed
    2s/4s/6s schedule (12s total across 3 attempts) never got close to that, so a rate limit
    used to fail the whole pipeline stage outright instead of just slowing down and succeeding
    on a later attempt. Any other transient error keeps the original short backoff."""
    if _is_rate_limited(exc):
        base = _suggested_retry_delay(exc)
        if base is None:
            base = 15 * (attempt + 1)
    else:
        base = 2 * (attempt + 1)
    jitter = random.uniform(0, base * 0.2)
    return min(base + jitter, MAX_BACKOFF_SECONDS)


class GeminiClient:
    def __init__(self, api_key: str | None = None):
        key = api_key or os.environ.get("GEMINI_API_KEY")
        if not key:
            raise RuntimeError(
                "No Gemini API key found. Set the GEMINI_API_KEY environment variable, or "
                "run the agent with --demo / DEMO_MODE=true to see sample output without "
                "making API calls."
            )
        self.client = genai.Client(api_key=key)

    def _generate_with_retry(self, config_kwargs: dict, user: str) -> str:
        """Shared retry loop for both generate_text and generate_json — see _backoff_seconds
        for why rate-limit errors get treated differently from other transient failures."""
        last_err: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
                response = self.client.models.generate_content(
                    model=MODEL,
                    contents=user,
                    config=types.GenerateContentConfig(**config_kwargs),
                )
                return (response.text or "").strip()
            except Exception as e:  # noqa: BLE001 - surfaced after retries are exhausted
                last_err = e
                if attempt < MAX_RETRIES - 1:
                    time.sleep(_backoff_seconds(attempt, e))

        if _is_rate_limited(last_err):
            raise RuntimeError(
                f"Gemini API rate limit (quota exceeded) still in effect after {MAX_RETRIES} "
                f"attempts with backoff — this is Google's per-minute free-tier cap on this "
                f"model, not an application bug. It usually clears within a minute; if it keeps "
                f"recurring, either the account needs a higher-quota (billed) Gemini API tier, "
                f"or requests need to be spread out more. Last error: {last_err}"
            )
        raise RuntimeError(f"Gemini API call failed after {MAX_RETRIES} attempts: {last_err}")

    def generate_text(self, system: str, user: str, max_tokens: int = 1500) -> str:
        return self._generate_with_retry(
            {"system_instruction": system, "max_output_tokens": max_tokens}, user
        )

    def generate_json(self, system: str, user: str, max_tokens: int = 2000) -> list | dict:
        raw = self._generate_with_retry(
            {
                "system_instruction": system,
                "max_output_tokens": max_tokens,
                "response_mime_type": "application/json",
            },
            user,
        )
        return parse_llm_json(raw, "Gemini")
