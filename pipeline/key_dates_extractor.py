"""
Pre-Phase-1 (non-fatal) check: extracts the uploaded bid document's key dates — question
deadline, submission deadline, evaluation period, award date — so they can be surfaced on the
Go/No-Go Summary tab (see main.py's project_detail route) without anyone having to re-read the
original RFP. Runs alongside the document-validity check and the response-structure check in
pipeline_runner.py, once per fresh upload; a failure here is logged and swallowed exactly like
the existing Go/No-Go capability-fit check — a bad extraction should never block the pipeline,
it just means the Summary tab shows no dates for that project.
"""
from __future__ import annotations

from dataclasses import dataclass

from claude_client import ClaudeClient
from key_dates_prompts import build_key_dates_prompt


@dataclass
class KeyDatesResult:
    question_deadline: str | None = None
    submission_deadline: str | None = None
    evaluation_period: str | None = None
    award_date: str | None = None

    def to_dict(self) -> dict:
        return {
            "question_deadline": self.question_deadline,
            "submission_deadline": self.submission_deadline,
            "evaluation_period": self.evaluation_period,
            "award_date": self.award_date,
        }

    def has_any(self) -> bool:
        return any([self.question_deadline, self.submission_deadline,
                    self.evaluation_period, self.award_date])


def _clean(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in ("null", "none", "n/a", "not specified", "not stated"):
        return None
    return text


def extract_key_dates(rfp_text: str, client: ClaudeClient) -> KeyDatesResult:
    system_prompt, user_prompt = build_key_dates_prompt(rfp_text)
    raw = client.generate_json(system_prompt, user_prompt, max_tokens=600)

    if not isinstance(raw, dict):
        raise RuntimeError(
            f"Expected a JSON object for key-dates extraction, got {type(raw)}: {raw}"
        )

    return KeyDatesResult(
        question_deadline=_clean(raw.get("question_deadline")),
        submission_deadline=_clean(raw.get("submission_deadline")),
        evaluation_period=_clean(raw.get("evaluation_period")),
        award_date=_clean(raw.get("award_date")),
    )
