"""
Pre-Phase-1 gate: confirms an uploaded file is actually a bid/RFP solicitation document before
any requirement extraction is attempted. This runs once per fresh upload, immediately after the
document's text has been read and before run_phase1() is ever called — if the document doesn't
pass, the pipeline halts right there (see pipeline_runner.py::_record_invalid_document) instead
of spending an extraction call on a document that was never a bid in the first place.
"""
from __future__ import annotations

from dataclasses import dataclass

from claude_client import ClaudeClient
from validity_prompts import build_validity_prompt


@dataclass
class DocumentValidityResult:
    is_bid_document: bool
    confidence: str
    reasoning: str

    def to_dict(self) -> dict:
        return {
            "is_bid_document": self.is_bid_document,
            "confidence": self.confidence,
            "reasoning": self.reasoning,
        }


def classify_document_validity(rfp_text: str, client: ClaudeClient) -> DocumentValidityResult:
    system_prompt, user_prompt = build_validity_prompt(rfp_text)
    raw = client.generate_json(system_prompt, user_prompt, max_tokens=300)

    if not isinstance(raw, dict):
        raise RuntimeError(
            f"Expected a JSON object for the document validity check, got {type(raw)}: {raw}"
        )

    return DocumentValidityResult(
        is_bid_document=bool(raw.get("is_bid_document", False)),
        confidence=raw.get("confidence", "low"),
        reasoning=raw.get("reasoning", "").strip(),
    )
