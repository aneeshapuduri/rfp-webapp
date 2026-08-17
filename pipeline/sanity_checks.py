"""
Rule-based sanity checks on a staffing/pricing plan. These are deliberately simple, explicit
rules (not another LLM call judging its own output) so the checks are predictable and
auditable. The goal is to catch obviously-wrong outputs — absurdly understaffed or
overstaffed plans — before they reach a client-facing document, not to validate pricing
strategy (that's the SA/leadership's job during review).
"""
from __future__ import annotations

from dataclasses import dataclass

from pricing_engine import PricingSummary

# Tunable thresholds — adjust based on your typical engagement size if these don't fit.
MIN_IMPLIED_FTE = 0.4     # below this, the team looks implausibly thin for the duration
MAX_IMPLIED_FTE = 30.0    # above this, re-check headcounts — likely a duplication error
MIN_ROLES_FOR_PROJECT = 3  # fewer than this suggests missing standard roles (PM, QA, etc.)
HOURS_PER_FTE_MONTH = 160  # standard full-time month


@dataclass
class SanityFlag:
    severity: str  # "warning" | "error"
    message: str


def run_sanity_checks(pricing: PricingSummary, project_duration_months: float) -> list[SanityFlag]:
    flags: list[SanityFlag] = []

    if project_duration_months <= 0:
        flags.append(SanityFlag("error", "Project duration is zero or negative — cannot compute implied team size."))
        return flags

    implied_fte = pricing.total_hours / (project_duration_months * HOURS_PER_FTE_MONTH)

    if implied_fte < MIN_IMPLIED_FTE:
        flags.append(SanityFlag(
            "error",
            f"Implied team size is only {implied_fte:.2f} FTE across {project_duration_months:.1f} months "
            f"({pricing.total_hours} total hours) — this looks too thin to realistically deliver the "
            f"scoped requirements. Review staffing hours before this reaches the client."
        ))
    elif implied_fte > MAX_IMPLIED_FTE:
        flags.append(SanityFlag(
            "warning",
            f"Implied team size is {implied_fte:.1f} FTE — unusually large. Check for duplicated or "
            f"inflated headcount/hours entries."
        ))

    if len(pricing.lines) < MIN_ROLES_FOR_PROJECT:
        flags.append(SanityFlag(
            "warning",
            f"Only {len(pricing.lines)} distinct role(s) staffed — most engagements this size need at "
            f"least a Program Manager, delivery role(s), and QA. Confirm nothing standard was omitted."
        ))

    role_names = {l.role for l in pricing.lines}
    if "Program Manager" not in role_names and pricing.total_hours > 500:
        flags.append(SanityFlag(
            "warning",
            "No Program Manager staffed despite a substantial effort estimate — confirm this is "
            "intentional (e.g. PM covered under a different existing contract)."
        ))

    zero_hour_lines = [l for l in pricing.lines if l.total_hours == 0]
    if zero_hour_lines:
        flags.append(SanityFlag(
            "error",
            f"Role(s) with zero total hours: {[l.role for l in zero_hour_lines]} — remove or fix before "
            f"this reaches pricing."
        ))

    return flags


def has_blocking_errors(flags: list[SanityFlag]) -> bool:
    return any(f.severity == "error" for f in flags)
