"""
Prompts for the remaining narrative sections a full proposal needs (executive summary,
understanding, timeline, past performance, closing) plus the compliance matrix — the section
this whole pipeline was designed to get right. The compliance matrix prompt requires the
model to address every requirement ID explicitly, which phase4_pipeline.py then verifies
against the actual Phase 1 requirement list rather than trusting the count.
"""

BASE_SYSTEM = """You are a senior proposal writer drafting a formal RFP response. Ground every
claim in the requirements and company profile given — never invent facts, certifications, or
past performance not provided. Confident, precise, plain business prose. No markdown headers."""


def executive_summary_prompt(project_title: str, agency: str, clear_requirements: list[str], company: dict) -> tuple[str, str]:
    reqs = "\n".join(f"- {r}" for r in clear_requirements)
    user = f"""PROJECT: {project_title}
AGENCY: {agency}
CONFIRMED REQUIREMENTS:
{reqs}
COMPANY PROFILE: {company}

Write the Executive Summary (350-450 words): understanding of the need, high-level solution,
2-3 concrete differentiators from the profile, confident close. No heading."""
    return BASE_SYSTEM, user


def understanding_prompt(project_title: str, agency: str, clear_requirements: list[str]) -> tuple[str, str]:
    reqs = "\n".join(f"- {r}" for r in clear_requirements)
    user = f"""PROJECT: {project_title}
AGENCY: {agency}
CONFIRMED REQUIREMENTS:
{reqs}

Write "Understanding of the Problem" (300-400 words) — demonstrate comprehension of the
underlying pain points and why this matters to stakeholders. No solutioning, no heading."""
    return BASE_SYSTEM, user


def timeline_prompt(clear_requirements: list[str], duration_months: float) -> tuple[str, str]:
    reqs = "\n".join(f"- {r}" for r in clear_requirements)
    user = f"""CONFIRMED REQUIREMENTS:
{reqs}
TOTAL PROJECT DURATION: {duration_months} months

Output ONLY a JSON array (no prose, no fences) of 5-8 phase objects, each with "phase",
"duration" (e.g. "Weeks 1-4"), "description" (1-2 sentences), covering discovery through
post-launch support, fitting within the stated duration."""
    return BASE_SYSTEM, user


def past_performance_prompt(clear_requirements: list[str], past_performance: list[dict]) -> tuple[str, str]:
    reqs = "\n".join(f"- {r}" for r in clear_requirements)
    user = f"""CONFIRMED REQUIREMENTS:
{reqs}
PAST PERFORMANCE EXAMPLES (use ONLY these, do not invent others):
{past_performance}

For each past performance example, write a 60-90 word paragraph naming client, contract
value, duration, and an explicit parallel to the current requirements. Use the client name
as a lead-in to each paragraph. No heading."""
    return BASE_SYSTEM, user


def closing_prompt(project_title: str, agency: str, contact: dict) -> tuple[str, str]:
    user = f"""PROJECT: {project_title}
AGENCY: {agency}
CONTACT: {contact}

Write a 100-150 word closing statement: commitment to the project, invitation for questions,
thanks, referencing the contact by name and title. No heading."""
    return BASE_SYSTEM, user


COMPLIANCE_SYSTEM = """You are a senior proposal writer completing a compliance matrix — the
most scrutinized section of a government RFP response. You MUST address every single
requirement ID given, with no exceptions and no omissions. Missing even one requirement in a
compliance matrix can disqualify a bid."""


def compliance_matrix_prompt(requirements: list[dict], company_capabilities: list[str]) -> tuple[str, str]:
    """requirements: list of {"id", "requirement"} — every finalized (non-ambiguous) requirement."""
    req_lines = "\n".join(f"- [{r['id']}] {r['requirement']}" for r in requirements)
    user = f"""REQUIREMENTS TO ADDRESS (every single one, by ID):
{req_lines}

COMPANY CAPABILITIES:
{chr(10).join(f'- {c}' for c in company_capabilities)}

For EVERY requirement ID listed above, output one JSON object with keys: "requirement_id"
(must exactly match an ID from the list above), "response" (1-2 sentences on how we meet it,
grounded in our capabilities), "status" (always "Full Compliance" unless a genuine conflict
exists, then "Partial Compliance" with the gap explained in the response).

Output ONLY a JSON array — no prose, no markdown fences. Every requirement ID from the list
above must appear exactly once in your output."""
    return COMPLIANCE_SYSTEM, user
