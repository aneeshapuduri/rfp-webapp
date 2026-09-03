"""
Phase 4: client-provided template handling — v1-limited, per config/template_spec.md.

Parses a client-uploaded .docx template's heading structure and fuzzy-matches each of our
12 standard sections to the closest heading. Anything below the confidence threshold is
flagged for manual placement rather than guessed — a wrong auto-placement in a submitted
proposal is worse than asking a human to place it. Full robustness (handling arbitrary
templates reliably) is scoped for a later phase once we've seen real client templates.
"""
from __future__ import annotations

import difflib

import docx

OUR_SECTIONS = [
    "Executive Summary and Bidder Overview",
    "Understanding of the Problem",
    "Company Overview",
    "Company Value Proposition",
    "Core Capabilities",
    "Technologies and Skillsets",
    "Company's Key Differentiators",
    "Partnership List",
    "Industry Recognition",
    "Relevant Past Performance",
    "Staffing Model and Project Initiation Readiness",
    "Scope of Services",
    "Out of Scope of Services",
    "Project Objectives",
    "Project Deliverables",
    "High-Level Solution Overview",
    "Project Plan and Milestones",
    "Project Change Management",
    "Performance Optimization Approach",
    "Key Personnel",
    "Proposed Team & Staffing Plan",
    "Maintenance and Support",
    "Operating Support Model",
    "Technical Assumptions",
    "General Assumptions",
    "Project Assumptions and Dependencies",
    "Effort & Pricing Summary",
    "Service Level Agreement (SLA)",
    "Service Boundaries and Scope Limitations",
    "Compliance Matrix",
    "Closing Statement",
]

MATCH_CONFIDENCE_THRESHOLD = 0.55


def extract_template_headings(template_docx_path: str) -> list[str]:
    d = docx.Document(template_docx_path)
    headings = []
    for p in d.paragraphs:
        if p.style.name.startswith("Heading") and p.text.strip():
            headings.append(p.text.strip())
    return headings


def map_sections_to_template(template_headings: list[str]) -> dict:
    """
    Returns {"matched": {our_section: template_heading}, "unmatched": [our_section, ...]}.
    Unmatched sections need manual placement in the client's template — this is intentional,
    not a bug, per the v1-limited scope.
    """
    matched = {}
    unmatched = []
    used_headings = set()

    for section in OUR_SECTIONS:
        best_score = 0.0
        best_heading = None
        for heading in template_headings:
            if heading in used_headings:
                continue
            score = difflib.SequenceMatcher(None, section.lower(), heading.lower()).ratio()
            if score > best_score:
                best_score = score
                best_heading = heading
        if best_heading and best_score >= MATCH_CONFIDENCE_THRESHOLD:
            matched[section] = best_heading
            used_headings.add(best_heading)
        else:
            unmatched.append(section)

    return {"matched": matched, "unmatched": unmatched}
