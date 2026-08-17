"""
Thin wrapper around the Google Gemini API, matching the exact same interface as
claude_client.ClaudeClient (generate_text / generate_json) so the rest of the pipeline never
needs to know which provider is actually running underneath it.
"""
import json
import os
import time

from google import genai
from google.genai import types

MODEL = "gemini-3.1-flash-lite"
MAX_RETRIES = 3


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

    def generate_text(self, system: str, user: str, max_tokens: int = 1500) -> str:
        last_err = None
        for attempt in range(MAX_RETRIES):
            try:
                response = self.client.models.generate_content(
                    model=MODEL,
                    contents=user,
                    config=types.GenerateContentConfig(
                        system_instruction=system,
                        max_output_tokens=max_tokens,
                    ),
                )
                return (response.text or "").strip()
            except Exception as e:  # noqa: BLE001 - surface after retries
                last_err = e
                time.sleep(2 * (attempt + 1))
        raise RuntimeError(f"Gemini API call failed after {MAX_RETRIES} attempts: {last_err}")

    def generate_json(self, system: str, user: str, max_tokens: int = 2000) -> list | dict:
        last_err = None
        for attempt in range(MAX_RETRIES):
            try:
                response = self.client.models.generate_content(
                    model=MODEL,
                    contents=user,
                    config=types.GenerateContentConfig(
                        system_instruction=system,
                        max_output_tokens=max_tokens,
                        response_mime_type="application/json",
                    ),
                )
                raw = (response.text or "").strip()
                if raw.startswith("```"):
                    raw = raw.split("```")[1]
                    if raw.startswith("json"):
                        raw = raw[4:]
                return json.loads(raw.strip())
            except json.JSONDecodeError as e:
                raise RuntimeError(f"Gemini did not return valid JSON: {e}\nRaw output:\n{raw}")
            except Exception as e:  # noqa: BLE001
                last_err = e
                time.sleep(2 * (attempt + 1))
        raise RuntimeError(f"Gemini API call failed after {MAX_RETRIES} attempts: {last_err}")
