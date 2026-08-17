"""
Builds the Phase 1 extraction + classification prompt. The ambiguity rules are read live from
config/ambiguity_criteria.md rather than duplicated as a string here, so the config file stays
the single source of truth — editing the criteria doc actually changes model behavior.
"""
from __future__ import annotations

import pathlib

_CRITERIA_PATH = pathlib.Path(__file__).resolve().parent.parent / "config" / "ambiguity_criteria.md"

SYSTEM_PROMPT = """You are a senior business analyst reviewing a government/enterprise RFP on
behalf of a bidding vendor. Your job is to extract every discrete requirement from the scope
of work and classify each one according to a strict set of ambiguity-detection rules. You are
conservative and precise: you do not soften genuine ambiguity into a false 'clear' rating just
to be helpful, because a missed ambiguity can cause the vendor to submit a proposal built on a
wrong assumption. Equally, you do not over-flag minor, standard-practice gaps as ambiguous when
a competent solutions architect could reasonably size and design against them — every
unnecessary clarification question sent to a client costs bid cycle time and looks
unprofessional. Follow the classification rules exactly as given."""


def build_extraction_prompt(rfp_text: str, scope_of_work: str) -> tuple[str, str]:
    criteria_text = _CRITERIA_PATH.read_text(encoding="utf-8")

    user_prompt = f"""AMBIGUITY DETECTION CRITERIA (follow these exactly):
{criteria_text}

FULL RFP TEXT (for context, e.g. background and cross-references):
{rfp_text}

SCOPE OF WORK SECTION (extract every discrete requirement from here):
{scope_of_work}

TASK:
Extract every discrete requirement from the Scope of Work section — typically one per
lettered/numbered item, but split further if a single item bundles multiple distinct
requirements. For each requirement, apply the classification rules above and produce one
JSON object with these exact keys: "requirement", "source_section", "status", "reasoning",
"clarification_question" (include only if status is "ambiguous", else omit or null),
"assumption_text" (include only if status is "assumption_needed", else omit or null).

Output ONLY a JSON array of these objects — no prose, no markdown code fences, no
commentary before or after. Do not skip any requirement in the scope of work."""

    return SYSTEM_PROMPT, user_prompt
