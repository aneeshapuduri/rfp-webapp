"""
Shared JSON-parsing helper for the LLM clients (claude_client.py, gemini_client.py).

Both clients ask the model for a single JSON value and then need to turn its raw text
response into real Python data. Two failure modes are common enough across providers to
handle in one place rather than duplicating (and inevitably drifting) the same fix-up logic
in each client:

1. The response is wrapped in a markdown code fence (```` ```json ... ``` ````) even when the
   prompt asked for JSON only.
2. The model emits one complete, valid JSON value and then keeps talking — a stray note, or
   (seen in practice with Gemini's flash-lite model on large staffing/pricing payloads) the
   same value repeated a second time. `json.loads` treats anything after the first value as a
   hard "Extra data" error, even though the value it needed was decoded correctly the first
   time. Recovering here means a large, otherwise-valid payload doesn't fail the whole
   pipeline stage over garbage that happens to follow it.
"""
from __future__ import annotations

import json


def parse_llm_json(raw: str, provider_name: str) -> list | dict:
    """Parse `raw` (a model's raw text response) as JSON, tolerating a markdown fence and
    trailing extra data after the first valid value. Raises RuntimeError, with the original
    raw output attached for debugging, if no valid JSON value can be recovered at all."""
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```")[1]
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
    cleaned = cleaned.strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        if e.msg == "Extra data":
            # The first e.pos characters already form one complete, valid JSON value —
            # decode just that and ignore whatever the model appended after it.
            try:
                value, _end = json.JSONDecoder().raw_decode(cleaned)
                return value
            except json.JSONDecodeError:
                pass
        raise RuntimeError(
            f"{provider_name} did not return valid JSON: {e}\nRaw output:\n{raw}"
        ) from e
