"""
Live, web-search-grounded market-rate research — one batched call per project run, covering
every role in that project's staffing plan (see phase3_pipeline.py's
_apply_market_rate_research, which calls this and applies the result as each staffing line's
starting hourly_rate override — see pricing_engine.build_pricing_summary's "hourly_rate" override
support). The result is also persisted as-is on projects.rate_research_json so the preview stage
can show a short sourcing note next to each role's (now editable) rate field.

Deliberately tolerant of a partial or malformed response: a role the model didn't return, or
returned with a non-numeric/non-positive rate, is simply left out of the result rather than
raising — the caller then leaves that one role at its static rate-card figure, exactly as if this
feature didn't exist for that role. The one thing this module never does is invent a number that
didn't come from the model's own response.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass, field

from rate_research_prompts import build_rate_research_prompt


@dataclass
class RateResearchResult:
    per_role: dict[str, dict] = field(default_factory=dict)  # {role: {"hourly_rate": float, "note": str}}
    researched_at: str = ""

    def to_dict(self) -> dict:
        return {"per_role": self.per_role, "researched_at": self.researched_at}


def research_market_rates(roles: list[str], agency: str, client) -> RateResearchResult:
    """`client` is any object implementing generate_json_with_search (ClaudeClient or
    GeminiClient both do) — not type-hinted to a specific class so this works with either
    provider, same as the rest of the pipeline."""
    system_prompt, user_prompt = build_rate_research_prompt(roles, agency)
    raw = client.generate_json_with_search(system_prompt, user_prompt, max_tokens=1500)

    if not isinstance(raw, dict):
        raise RuntimeError(
            f"Expected a JSON object for market rate research, got {type(raw)}: {raw}"
        )

    known_roles = set(roles)
    per_role: dict[str, dict] = {}
    for entry in raw.get("roles") or []:
        if not isinstance(entry, dict):
            continue
        role = str(entry.get("role", "")).strip()
        rate = entry.get("hourly_rate")
        if role not in known_roles:
            continue
        if not isinstance(rate, (int, float)) or rate <= 0:
            continue
        per_role[role] = {
            "hourly_rate": round(float(rate), 2),
            "note": str(entry.get("note", "")).strip(),
        }

    return RateResearchResult(
        per_role=per_role,
        researched_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
    )
