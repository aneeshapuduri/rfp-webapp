"""
Defines the structured output contract for Phase 1 (Intake & Requirement Extraction).
This is the schema Phase 2 (decision gate) and Phase 3 (SA engine) consume, so it's kept
strict and validated rather than trusting raw model output as-is.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Literal

Status = Literal["clear", "ambiguous", "assumption_needed"]
VALID_STATUSES = {"clear", "ambiguous", "assumption_needed"}


@dataclass
class RequirementItem:
    id: str
    requirement: str
    source_section: str
    status: Status
    reasoning: str
    clarification_question: str | None = None
    assumption_text: str | None = None
    client_response: str | None = None
    escalated_for_manual_review: bool = False

    def validate(self) -> list[str]:
        errors = []
        if self.status not in VALID_STATUSES:
            errors.append(f"{self.id}: invalid status '{self.status}'")
        if self.status == "ambiguous" and not self.clarification_question:
            errors.append(f"{self.id}: status is 'ambiguous' but clarification_question is missing")
        if self.status == "assumption_needed" and not self.assumption_text:
            errors.append(f"{self.id}: status is 'assumption_needed' but assumption_text is missing")
        if not self.requirement.strip():
            errors.append(f"{self.id}: empty requirement text")
        return errors


@dataclass
class Phase1Result:
    project_title: str
    agency: str
    requirements: list[RequirementItem] = field(default_factory=list)

    @property
    def summary(self) -> dict:
        counts = {"clear": 0, "ambiguous": 0, "assumption_needed": 0}
        for r in self.requirements:
            counts[r.status] = counts.get(r.status, 0) + 1
        return {"total": len(self.requirements), **counts}

    @property
    def pipeline_decision(self) -> Literal["proceed", "halt_for_clarification"]:
        return "halt_for_clarification" if self.summary["ambiguous"] > 0 else "proceed"

    def get_ambiguous(self) -> list[RequirementItem]:
        return [r for r in self.requirements if r.status == "ambiguous"]

    def get_escalated(self) -> list[RequirementItem]:
        return [r for r in self.requirements if r.escalated_for_manual_review]

    def by_id(self, req_id: str) -> RequirementItem | None:
        return next((r for r in self.requirements if r.id == req_id), None)

    def validate(self) -> list[str]:
        """Returns a list of validation error strings; empty list means valid."""
        errors = []
        if not self.requirements:
            errors.append("No requirements extracted — extraction likely failed silently.")
        ids_seen = set()
        for r in self.requirements:
            errors.extend(r.validate())
            if r.id in ids_seen:
                errors.append(f"Duplicate requirement id: {r.id}")
            ids_seen.add(r.id)
        return errors

    def to_dict(self) -> dict:
        return {
            "project_title": self.project_title,
            "agency": self.agency,
            "summary": self.summary,
            "pipeline_decision": self.pipeline_decision,
            "requirements": [asdict(r) for r in self.requirements],
        }

    @staticmethod
    def from_raw(project_title: str, agency: str, raw_items: list[dict]) -> "Phase1Result":
        items = []
        for i, raw in enumerate(raw_items, start=1):
            items.append(RequirementItem(
                id=f"REQ-{i:03d}",
                requirement=raw.get("requirement", "").strip(),
                source_section=raw.get("source_section", "").strip(),
                status=raw.get("status", "clear"),
                reasoning=raw.get("reasoning", "").strip(),
                clarification_question=raw.get("clarification_question") or None,
                assumption_text=raw.get("assumption_text") or None,
            ))
        return Phase1Result(project_title=project_title, agency=agency, requirements=items)

    @staticmethod
    def from_dict(d: dict) -> "Phase1Result":
        """Reconstructs a Phase1Result from Phase1Result.to_dict() output (e.g. loaded from
        the database), preserving requirement IDs, client responses, and escalation flags."""
        reqs = [RequirementItem(**item) for item in d["requirements"]]
        return Phase1Result(project_title=d["project_title"], agency=d["agency"], requirements=reqs)


@dataclass
class Phase3Result:
    """Output of the Solution Architecture engine: technical approach, staffing, pricing."""
    technology_approach: str
    assumptions: list[str]
    pricing: dict  # PricingSummary.to_dict()
    sanity_flags: list[dict]  # [{"severity": ..., "message": ...}]
    duration_months: float

    def to_dict(self) -> dict:
        return {
            "technology_approach": self.technology_approach,
            "assumptions": self.assumptions,
            "pricing": self.pricing,
            "sanity_flags": self.sanity_flags,
            "duration_months": self.duration_months,
            "has_blocking_errors": any(f["severity"] == "error" for f in self.sanity_flags),
        }
