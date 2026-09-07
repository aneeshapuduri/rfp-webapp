"""
Pre-Phase-1 (non-fatal) check: detects whether the uploaded bid document itself specifies a
required structure for the vendor's written response. Runs alongside the document-validity
check in pipeline_runner.py, once per fresh upload — its result is stored on
projects.rfp_structure_json and, if a structure was found, offered as a third document-generation
option ("RFP-Specified Structure") alongside the default template and an uploaded custom
template (see main.py's _read_template_choice and template_choice radio group in
project_detail.html). Unlike the validity check, a failure here should never block the pipeline
— see pipeline_runner.py's _detect_rfp_structure, which wraps this in a try/except exactly like
the existing Go/No-Go capability-fit check.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from claude_client import ClaudeClient
from response_structure_prompts import build_response_structure_prompt


@dataclass
class ResponseStructureResult:
    detected: bool
    sections: list[str] = field(default_factory=list)
    reasoning: str = ""

    def to_dict(self) -> dict:
        return {
            "detected": self.detected,
            "sections": self.sections,
            "reasoning": self.reasoning,
        }


def detect_response_structure(rfp_text: str, client: ClaudeClient) -> ResponseStructureResult:
    system_prompt, user_prompt = build_response_structure_prompt(rfp_text)
    raw = client.generate_json(system_prompt, user_prompt, max_tokens=800)

    if not isinstance(raw, dict):
        raise RuntimeError(
            f"Expected a JSON object for the response-structure check, got {type(raw)}: {raw}"
        )

    sections = [str(s).strip() for s in (raw.get("sections") or []) if str(s).strip()]
    detected = bool(raw.get("structure_specified", False)) and bool(sections)

    return ResponseStructureResult(
        detected=detected,
        sections=sections if detected else [],
        reasoning=raw.get("reasoning", "").strip(),
    )
