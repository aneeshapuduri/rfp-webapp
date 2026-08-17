"""
Phase 1: Intake & Requirement Extraction Engine (SBA role).

Reads a bid invitation, extracts every discrete requirement from the scope of work, and
classifies each as clear / ambiguous / assumption_needed per config/ambiguity_criteria.md.
Produces a validated Phase1Result that Phase 2 (decision gate) consumes directly.
"""
from __future__ import annotations

import re

from claude_client import ClaudeClient
from extraction_prompts import build_extraction_prompt
from schema import Phase1Result
from structural_parser import get_scope_of_work


def guess_project_title(rfp_text: str) -> str:
    m = re.search(r"PROJECT TITLE:\s*(.+)", rfp_text, re.IGNORECASE)
    return m.group(1).strip() if m else "Untitled Project"


def guess_agency(rfp_text: str) -> str:
    m = re.search(r"Issuing Agency:\s*(.+)", rfp_text, re.IGNORECASE)
    return m.group(1).strip() if m else "Unknown Agency"


def run_phase1(rfp_text: str, client: ClaudeClient) -> Phase1Result:
    project_title = guess_project_title(rfp_text)
    agency = guess_agency(rfp_text)
    scope_of_work = get_scope_of_work(rfp_text)

    system_prompt, user_prompt = build_extraction_prompt(rfp_text, scope_of_work)
    raw_items = client.generate_json(system_prompt, user_prompt, max_tokens=4000)

    if not isinstance(raw_items, list):
        raise RuntimeError(
            f"Expected a JSON array of requirement objects, got {type(raw_items)}: {raw_items}"
        )

    result = Phase1Result.from_raw(project_title, agency, raw_items)

    errors = result.validate()
    if errors:
        raise RuntimeError(
            "Phase 1 output failed validation — refusing to hand invalid data to Phase 2:\n"
            + "\n".join(f"  - {e}" for e in errors)
        )

    return result
