"""
Deterministic pricing calculation. Takes a staffing plan (role, headcount, hours_per_person —
whether from a live Claude call or demo data) and the Phase 0 rate card, and computes costs
with plain arithmetic. This file intentionally contains zero LLM calls: dollar figures in a
bid must be exactly reproducible from the inputs, not subject to model variance.
"""
from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass

RATE_CARD_PATH = pathlib.Path(__file__).resolve().parent.parent / "config" / "role_rate_card.json"


@dataclass
class StaffingLine:
    role: str
    headcount: int
    hours_per_person: int
    hourly_rate: float
    rationale: str = ""

    @property
    def total_hours(self) -> int:
        return self.headcount * self.hours_per_person

    @property
    def subtotal(self) -> float:
        return round(self.total_hours * self.hourly_rate, 2)


@dataclass
class PricingSummary:
    lines: list[StaffingLine]
    contingency_pct: float

    @property
    def labor_subtotal(self) -> float:
        return round(sum(l.subtotal for l in self.lines), 2)

    @property
    def contingency_amount(self) -> float:
        return round(self.labor_subtotal * (self.contingency_pct / 100), 2)

    @property
    def total(self) -> float:
        return round(self.labor_subtotal + self.contingency_amount, 2)

    @property
    def total_hours(self) -> int:
        return sum(l.total_hours for l in self.lines)

    def to_dict(self) -> dict:
        return {
            "lines": [
                {
                    "role": l.role,
                    "headcount": l.headcount,
                    "hours_per_person": l.hours_per_person,
                    "total_hours": l.total_hours,
                    "hourly_rate": l.hourly_rate,
                    "subtotal": l.subtotal,
                    "rationale": l.rationale,
                }
                for l in self.lines
            ],
            "labor_subtotal": self.labor_subtotal,
            "contingency_pct": self.contingency_pct,
            "contingency_amount": self.contingency_amount,
            "total": self.total,
            "total_hours": self.total_hours,
        }


def load_rate_card() -> dict:
    return json.loads(RATE_CARD_PATH.read_text(encoding="utf-8"))


def build_pricing_summary(staffing_plan: list[dict]) -> PricingSummary:
    """
    staffing_plan: list of {"role", "headcount", "hours_per_person", "rationale"} — the raw
    output from the SA staffing prompt (or demo data). Looks up each role's rate from the
    Phase 0 rate card; raises if a role isn't in the catalog rather than guessing a rate.
    """
    rate_card = load_rate_card()
    rate_lookup = {r["role"]: r["blended_hourly_rate"] for r in rate_card["roles"]}
    contingency_pct = rate_card["pricing_rules"]["contingency_buffer_pct"]

    lines = []
    for item in staffing_plan:
        role = item["role"]
        if role not in rate_lookup:
            raise ValueError(
                f"Role '{role}' is not in the rate card catalog — refusing to guess a rate. "
                f"Valid roles: {sorted(rate_lookup.keys())}"
            )
        lines.append(StaffingLine(
            role=role,
            headcount=int(item["headcount"]),
            hours_per_person=int(item["hours_per_person"]),
            hourly_rate=rate_lookup[role],
            rationale=item.get("rationale", ""),
        ))

    return PricingSummary(lines=lines, contingency_pct=contingency_pct)
