"""
Builds the prompt for the fresh, LLM-based Go/No-Go recommendation shown on the Summary tab (see
main.py's project_detail route and templates/project_detail.html) immediately above the admin's
own Approve / Reject / Need Confirmation decision.

This is deliberately separate from, and later than, pipeline/go_no_go.py's
assess_capability_fit() — that check is a cheap, deterministic keyword-overlap score run right
after Phase 1 (before ambiguity resolution, before a compliance matrix or categorized RFP summary
even exist), shown in its own "Go / No-Go Decision" card higher up the page. By the time a project
reaches the Summary tab, a lot more is known — the actual compliance matrix (how our drafted
response addresses each requirement, not just whether keywords overlap), the categorized RFP
summary (bidder qualifications, cost proposal terms, contract terms — the kind of thing that sinks
a bid even when the technical fit is fine), and the assumptions the AI had to make to fill gaps.
This prompt asks the model to weigh all of that together and give one fresh, holistic read,
right before the human decision it's meant to inform.
"""
from __future__ import annotations

import json

MAX_CHARS_PER_FIELD = 2000

SYSTEM_PROMPT = """You are a senior bid manager giving a final go/no-go recommendation to a
decision-maker who is about to approve, reject, or send back for revision a drafted proposal
response — this is the last automated read before a human decides. You're given: the earlier
capability-fit read (a fast keyword-based check done right after the RFP was first read), the
compliance matrix showing how the drafted response actually addresses each requirement, a
categorized summary of the RFP's bidder qualifications / cost proposal / evaluation process /
contract terms, and the assumptions the drafting process had to make to fill gaps the RFP didn't
specify.

Weigh all of this together — not just technical capability, but also whether the qualifications,
cost structure, evaluation approach, and contract terms look bidable and whether the assumptions
made are reasonable or risky — and give ONE overall recommendation:
- "Go" — a strong, low-risk fit; proceed with confidence.
- "Go, with gaps" — bidable, but there are real gaps, risky assumptions, or unfavorable terms a
  human should weigh before committing.
- "No-Go" — significant misalignment (capability, qualifications, cost terms, or contract terms)
  that makes this a poor bid candidate.

Be specific and reference the actual input given — a generic recommendation is not useful here."""


def build_go_no_go_suggestion_prompt(
    project_title: str,
    agency: str,
    capability_fit: dict | None,
    compliance_matrix: list[dict] | None,
    rfp_summary: dict | None,
    assumptions: list[str] | None,
) -> tuple[str, str]:
    def _trim(value):
        if value is None:
            return None
        text = str(value)
        return text[:MAX_CHARS_PER_FIELD]

    compliance_matrix = compliance_matrix or []
    matched = sum(1 for m in compliance_matrix if "Full" in (m.get("status") or ""))
    not_matched = [
        m.get("requirement", "") for m in compliance_matrix if "Full" not in (m.get("status") or "")
    ]

    payload = {
        "project_title": project_title,
        "agency": agency,
        "early_capability_fit": {
            "overall": capability_fit.get("overall"),
            "coverage_pct": capability_fit.get("coverage_pct"),
            "reasoning": _trim(capability_fit.get("reasoning")),
        } if capability_fit else None,
        "compliance_matrix_summary": {
            "matched": matched,
            "not_matched_count": len(compliance_matrix) - matched,
            "total": len(compliance_matrix),
            "not_matched_requirements": not_matched[:25],
        } if compliance_matrix else None,
        "rfp_summary": {k: _trim(v) for k, v in (rfp_summary or {}).items() if v} or None,
        "drafting_assumptions": (assumptions or [])[:25],
    }

    user_prompt = f"""INPUT (JSON):
{json.dumps(payload, indent=2)}

TASK:
Give one overall Go/No-Go recommendation for this project based on everything above.

Output ONLY a single JSON object with these exact keys, no prose, no markdown code fences, no
commentary before or after:
{{
  "overall": "Go" | "Go, with gaps" | "No-Go",
  "reasoning": "<2-4 sentences explaining the recommendation, referencing specific input above>",
  "key_considerations": ["<short specific point a human should weigh>", "..."]
}}"""

    return SYSTEM_PROMPT, user_prompt
