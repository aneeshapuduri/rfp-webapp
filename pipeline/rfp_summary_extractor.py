"""
Pre-Phase-1 (non-fatal) check: extracts a categorized summary of the uploaded bid document —
vendor/bidder info, bidder qualifications, cost proposal instructions, key events, evaluation
process, submission requirements, other proposal requirements, and contract terms & conditions —
so a reviewer can see what the RFP actually asks for on the Go/No-Go Summary tab (see main.py's
project_detail route) without re-reading the original document. Runs alongside the document-
validity check, the response-structure check, and the key-dates extraction in pipeline_runner.py,
once per fresh upload; a failure here is logged and swallowed exactly like those checks — a bad
extraction should never block the pipeline, it just means the Summary tab shows less context for
that project.
"""
from __future__ import annotations

from dataclasses import dataclass

from claude_client import ClaudeClient
from rfp_summary_prompts import build_rfp_summary_prompt

_FIELDS = (
    "vendor_info",
    "bidder_qualifications",
    "cost_proposal",
    "key_events",
    "evaluation_process",
    "submission_requirements",
    "other_requirements",
    "contract_terms",
)


@dataclass
class RFPSummaryResult:
    vendor_info: str | None = None
    bidder_qualifications: str | None = None
    cost_proposal: str | None = None
    key_events: str | None = None
    evaluation_process: str | None = None
    submission_requirements: str | None = None
    other_requirements: str | None = None
    contract_terms: str | None = None

    def to_dict(self) -> dict:
        return {field: getattr(self, field) for field in _FIELDS}

    def has_any(self) -> bool:
        return any(getattr(self, field) for field in _FIELDS)


def _clean(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in ("null", "none", "n/a", "not specified", "not stated"):
        return None
    return text


def extract_rfp_summary(rfp_text: str, client: ClaudeClient) -> RFPSummaryResult:
    system_prompt, user_prompt = build_rfp_summary_prompt(rfp_text)
    raw = client.generate_json(system_prompt, user_prompt, max_tokens=2500)

    if not isinstance(raw, dict):
        raise RuntimeError(
            f"Expected a JSON object for RFP-summary extraction, got {type(raw)}: {raw}"
        )

    return RFPSummaryResult(**{field: _clean(raw.get(field)) for field in _FIELDS})
