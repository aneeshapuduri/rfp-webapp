"""
Identifies the major structural sections of an RFP document using heuristic pattern matching
on common section headers. This gives the requirement-extraction prompt a scoped "Scope of
Work" chunk to focus on, rather than feeding the whole document and hoping the model finds
the right part — and it's what lets us report which section a requirement came from.
"""
from __future__ import annotations

import re

SECTION_PATTERNS = {
    "background": r"(?im)^\s*\d*\.?\s*BACKGROUND\b",
    "scope_of_work": r"(?im)^\s*\d*\.?\s*SCOPE OF WORK\b",
    "proposal_requirements": r"(?im)^\s*\d*\.?\s*PROPOSAL REQUIREMENTS?\b",
    "evaluation_criteria": r"(?im)^\s*\d*\.?\s*EVALUATION CRITERIA\b",
    "submission_deadline": r"(?im)^\s*\d*\.?\s*SUBMISSION\b",
    "point_of_contact": r"(?im)^\s*\d*\.?\s*(POINT OF CONTACT|CONTACT)\b",
}


def split_sections(rfp_text: str) -> dict[str, str]:
    """
    Returns a dict mapping known section names to their text content, based on where each
    recognized header appears. Text between one recognized header and the next belongs to
    the first. Anything before the first recognized header goes under 'preamble'.
    """
    matches = []
    for name, pattern in SECTION_PATTERNS.items():
        for m in re.finditer(pattern, rfp_text):
            matches.append((m.start(), name))
    matches.sort(key=lambda x: x[0])

    if not matches:
        return {"full_text": rfp_text}

    sections: dict[str, str] = {}
    if matches[0][0] > 0:
        sections["preamble"] = rfp_text[: matches[0][0]].strip()

    for i, (start, name) in enumerate(matches):
        end = matches[i + 1][0] if i + 1 < len(matches) else len(rfp_text)
        # Avoid overwriting if the same section header appears twice; append instead.
        chunk = rfp_text[start:end].strip()
        sections[name] = (sections[name] + "\n" + chunk) if name in sections else chunk

    return sections


def get_scope_of_work(rfp_text: str) -> str:
    """Returns just the scope-of-work section if identifiable, else the full text as fallback."""
    sections = split_sections(rfp_text)
    return sections.get("scope_of_work") or sections.get("full_text") or rfp_text
