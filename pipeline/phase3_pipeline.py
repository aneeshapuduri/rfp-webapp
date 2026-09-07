"""
Phase 3: Solution Architecture Engine (SA role).

Takes a Phase1Result where all requirements are clear/assumption_needed (no remaining
ambiguous items — Phase 2's gate must have already passed) and produces the technology
approach narrative, staffing plan, priced summary, and sanity-check flags.
"""
from __future__ import annotations

import json
import logging
import pathlib

from claude_client import ClaudeClient
from pricing_engine import build_pricing_summary, load_rate_card
from rate_research import research_market_rates
from sa_prompts import staffing_estimate_prompt, technology_approach_prompt
from sanity_checks import run_sanity_checks
from schema import Phase1Result, Phase3Result

logger = logging.getLogger("rfp_agent.pipeline.phase3")

COMPANY_PROFILE_PATH = pathlib.Path(__file__).resolve().parent.parent / "config" / "company_profile.json"


def load_company_profile() -> dict:
    return json.loads(COMPANY_PROFILE_PATH.read_text(encoding="utf-8"))


def run_phase3(
    result: Phase1Result,
    duration_months: float,
    client: ClaudeClient | None = None,
    demo_technology_approach: str | None = None,
    demo_staffing_plan: list[dict] | None = None,
) -> Phase3Result:
    if result.pipeline_decision != "proceed":
        raise RuntimeError(
            "Phase 3 refuses to run while requirements are still ambiguous — "
            f"current decision: {result.pipeline_decision}. Resolve via Phase 2 first."
        )

    clear_requirements = [r.requirement for r in result.requirements if r.status == "clear"]
    assumptions = [r.assumption_text for r in result.requirements if r.assumption_text]
    company = load_company_profile()
    rate_card = load_rate_card()

    if demo_technology_approach is not None:
        technology_approach = demo_technology_approach
    else:
        if client is None:
            raise RuntimeError("No Claude client and no demo_technology_approach supplied.")
        sys_p, user_p = technology_approach_prompt(clear_requirements, company["core_capabilities"])
        technology_approach = client.generate_text(sys_p, user_p, max_tokens=1500)

    rate_research = None
    if demo_staffing_plan is not None:
        staffing_plan = demo_staffing_plan
    else:
        if client is None:
            raise RuntimeError("No Claude client and no demo_staffing_plan supplied.")
        duration_hint = f"Approximately {duration_months} months"
        sys_p, user_p = staffing_estimate_prompt(clear_requirements, rate_card["roles"], duration_hint)
        staffing_plan = client.generate_json(sys_p, user_p, max_tokens=2000)
        rate_research = _apply_market_rate_research(staffing_plan, result.agency, client)

    pricing = build_pricing_summary(staffing_plan)
    flags = run_sanity_checks(pricing, duration_months)

    return Phase3Result(
        technology_approach=technology_approach,
        assumptions=assumptions,
        pricing=pricing.to_dict(),
        sanity_flags=[{"severity": f.severity, "message": f.message} for f in flags],
        duration_months=duration_months,
        rate_research=rate_research,
    )


def _apply_market_rate_research(staffing_plan: list[dict], agency: str, client: ClaudeClient) -> dict | None:
    """The user asked that the INITIAL rate the agent proposes for each staffing role always
    reflect recent market research, not just the static config/role_rate_card.json figures —
    with the rate then left editable by a human in the preview stage (see main.py's
    _reassemble_preview_content). Mutates staffing_plan in place, adding an "hourly_rate" key to
    each item that pricing_engine.build_pricing_summary treats as an override; a role research
    didn't return anything usable for is simply left without that key, so it falls back to the
    rate card exactly as if this feature didn't exist for that one role.

    Never fatal — research_market_rates already falls back gracefully at the LLM-client level
    (see claude_client.py / gemini_client.py's generate_json_with_search), but this is one more
    layer of safety: a bug anywhere in the research/parsing path should never block Phase 3 from
    producing a priced proposal, it should just mean this project's rates come entirely from the
    static rate card, same as before this feature existed."""
    roles = sorted({str(item["role"]) for item in staffing_plan})
    try:
        research = research_market_rates(roles, agency, client)
    except Exception:  # noqa: BLE001
        logger.exception("Market rate research failed (non-fatal) — using static rate card")
        return None

    if not research.per_role:
        return None

    for item in staffing_plan:
        found = research.per_role.get(str(item["role"]))
        if found:
            item["hourly_rate"] = found["hourly_rate"]

    return research.to_dict()
