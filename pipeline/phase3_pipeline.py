"""
Phase 3: Solution Architecture Engine (SA role).

Takes a Phase1Result where all requirements are clear/assumption_needed (no remaining
ambiguous items — Phase 2's gate must have already passed) and produces the technology
approach narrative, staffing plan, priced summary, and sanity-check flags.
"""
from __future__ import annotations

import json
import pathlib

from claude_client import ClaudeClient
from pricing_engine import build_pricing_summary, load_rate_card
from sa_prompts import staffing_estimate_prompt, technology_approach_prompt
from sanity_checks import run_sanity_checks
from schema import Phase1Result, Phase3Result

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

    if demo_staffing_plan is not None:
        staffing_plan = demo_staffing_plan
    else:
        if client is None:
            raise RuntimeError("No Claude client and no demo_staffing_plan supplied.")
        duration_hint = f"Approximately {duration_months} months"
        sys_p, user_p = staffing_estimate_prompt(clear_requirements, rate_card["roles"], duration_hint)
        staffing_plan = client.generate_json(sys_p, user_p, max_tokens=2000)

    pricing = build_pricing_summary(staffing_plan)
    flags = run_sanity_checks(pricing, duration_months)

    return Phase3Result(
        technology_approach=technology_approach,
        assumptions=assumptions,
        pricing=pricing.to_dict(),
        sanity_flags=[{"severity": f.severity, "message": f.message} for f in flags],
        duration_months=duration_months,
    )
