"""
Builds the live market-rate research prompt. Run once per project during Phase 3 (see
phase3_pipeline.py's _apply_market_rate_research), right after the staffing plan's roles are
known and before pricing is computed — the user asked that the INITIAL rate the agent proposes
for each role always reflect recent market research, not just the static config/role_rate_card.json
figures, with the rate then left editable in the preview stage (see main.py's
_reassemble_preview_content and the staffing table in project_detail.html).

This is a single batched call covering every role in the staffing plan at once (rather than one
call per role) — cheaper, and lets the model see the full role list for context (e.g. seniority
relative to other roles on the same engagement) while still asking for an independent rate per
role.
"""
from __future__ import annotations

SYSTEM_PROMPT = """You are a pricing analyst at an IT consulting firm that bids on government and
enterprise RFPs. You research CURRENT market hourly bill rates for specific staffing roles on
comparable engagements, using web search to ground your answer in recent, real published data —
industry rate/salary surveys, contractor marketplace data, published consulting benchmarks —
rather than relying only on your own training data, which can be out of date. For each role you
propose ONE blended hourly bill rate: fully loaded (covers overhead and margin), set at a
competitive-bid midpoint of what you find (not the top of the market — proposals are won on being
competitive, not on billing the maximum defensible rate). You are conservative: if you can't find
good current data for a role, say so in your note and give your best reasoned estimate anyway —
never leave a role out entirely."""


def build_rate_research_prompt(roles: list[str], agency: str = "") -> tuple[str, str]:
    role_list = "\n".join(f"- {r}" for r in roles)
    engagement = f" for a proposal to {agency}" if agency else ""

    user_prompt = f"""Research current market hourly bill rates for the following staffing roles on a
government/enterprise IT consulting engagement{engagement}:
{role_list}

For each role, search for recent published rate benchmarks (rate surveys, contractor
marketplaces, consulting industry reports) and propose one blended hourly bill rate in US
dollars.

Output ONLY a single JSON object with this exact shape, no prose, no markdown code fences, no
commentary before or after:
{{
  "roles": [
    {{
      "role": "<role name, exactly as given above>",
      "hourly_rate": <number, no currency symbol>,
      "note": "one sentence citing what you found and why you landed on this figure"
    }}
  ]
}}

Include every role listed above, in any order. Use the role name exactly as given so it can be
matched back to the staffing plan."""

    return SYSTEM_PROMPT, user_prompt
