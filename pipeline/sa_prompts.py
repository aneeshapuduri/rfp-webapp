"""
Prompts for the Phase 3 Solution Architecture engine. Two calls: one for the technology
approach narrative, one for staffing/effort estimation (kept separate from pricing — pricing
math is done in pricing_engine.py in plain code, never left to the model to compute).
"""

SYSTEM_PROMPT = """You are a senior solutions architect at a professional services firm,
scoping a response to a government/enterprise RFP. Ground every recommendation in the
requirements given and the company's actual core capabilities — never invent technology
choices unrelated to what the company can credibly deliver. Be specific and decisive, not
hedging or vague; the client is evaluating whether you understand how to build this."""


def technology_approach_prompt(clear_requirements: list[str], company_capabilities: list[str]) -> tuple[str, str]:
    reqs = "\n".join(f"- {r}" for r in clear_requirements)
    caps = "\n".join(f"- {c}" for c in company_capabilities)
    user = f"""CONFIRMED REQUIREMENTS (all ambiguities already resolved):
{reqs}

COMPANY CORE CAPABILITIES:
{caps}

Write the Technical Approach & Solution Architecture narrative (500-700 words) addressing how
we will approach each major requirement area, organized into short labeled sub-sections
(e.g. "Solution Architecture:", "Integration Approach:", "Data Migration:",
"Security & Compliance:", "Quality Assurance:") — only include sub-sections relevant to what's
actually in the requirements. No top-level heading, plain paragraphs, no markdown."""
    return SYSTEM_PROMPT, user


def staffing_estimate_prompt(
    clear_requirements: list[str], role_catalog: list[dict], project_duration_hint: str
) -> tuple[str, str]:
    reqs = "\n".join(f"- {r}" for r in clear_requirements)
    roles = "\n".join(f"- {r['role']}" for r in role_catalog)
    user = f"""CONFIRMED REQUIREMENTS:
{reqs}

AVAILABLE ROLE CATALOG (use ONLY these role names, exactly as written):
{roles}

PROJECT DURATION SIGNAL FROM THE RFP: {project_duration_hint}

Estimate the staffing plan needed to deliver this project. For each role actually needed
(don't include roles that aren't relevant to this project's scope), output one JSON object
with keys: "role" (must exactly match a name from the catalog above), "headcount" (integer,
number of people in that role), "hours_per_person" (integer, total hours that person works
across the full project), "rationale" (1 sentence on why this role/hours level is needed).

Output ONLY a JSON array of these objects — no prose, no markdown fences, no pricing
calculations (those are handled separately)."""
    return SYSTEM_PROMPT, user
