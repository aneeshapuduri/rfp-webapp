"""
Phase 1.5: Go / No-Go capability-fit check.

The original mockup (rfp_agent_mockup.html) showed a "Go / No-Go Decision" panel that checked
an RFP's requirements against the company's stated service-offering capabilities and rendered
a bid/no-bid recommendation — but no backend for it ever existed; it was front-end concept art
with hardcoded example output. This module is the real thing, run once per project right after
Phase 1 extraction (before the ambiguity gate decides whether to halt for clarification), so
the bid team sees a fit assessment immediately alongside the extracted requirements.

Deliberately deterministic and keyword-based rather than another LLM call: a go/no-bid signal
should be explainable ("these requirements don't match anything we do" beats an opaque score),
reproducible without burning API budget on every upload, and available even in DEMO_MODE with
no API key configured at all.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict

from pipeline.schema import Phase1Result

_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "for", "in", "on", "with", "by", "at", "as",
    "is", "are", "be", "must", "shall", "should", "will", "may", "this", "that", "all", "any",
    "vendor", "vendors", "proposer", "proposers", "contractor", "respondent", "provide",
    "provided", "providing", "required", "requirement", "requirements", "system", "systems",
    "service", "services", "including", "include", "includes", "support", "supports", "able",
    "ability", "solution", "project", "county", "city", "agency", "district", "department",
    "current", "existing", "new", "each", "which", "their", "its", "into", "from", "such",
    "per", "within", "not", "can", "have", "has", "had", "over", "under", "than", "also",
}

_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9/+#\.]*")


def _tokenize(text: str) -> set[str]:
    tokens = set()
    for raw in _TOKEN_RE.findall(text or ""):
        tok = raw.strip(".").lower()
        if len(tok) < 3:
            continue
        if tok in _STOPWORDS:
            continue
        tokens.add(tok)
    return tokens


def _capability_keywords(capability_text: str) -> set[str]:
    """A capability string like 'Cloud infrastructure modernization (AWS, Azure, GovCloud)'
    yields keywords from both the main phrase and anything in parentheses, since the
    parenthetical is often the most specific/matchable part."""
    return _tokenize(capability_text.replace("(", " ").replace(")", " ").replace(",", " "))


@dataclass
class CapabilityGap:
    requirement_id: str
    requirement: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CapabilityFit:
    overall: str  # "Go" | "Go, with gaps" | "No-Go"
    reasoning: str
    coverage_pct: float
    matched_capabilities: list[str] = field(default_factory=list)
    unmatched_capabilities: list[str] = field(default_factory=list)
    gaps: list[CapabilityGap] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "overall": self.overall,
            "reasoning": self.reasoning,
            "coverage_pct": self.coverage_pct,
            "matched_capabilities": self.matched_capabilities,
            "unmatched_capabilities": self.unmatched_capabilities,
            "gaps": [g.to_dict() for g in self.gaps],
        }

    @staticmethod
    def from_dict(d: dict) -> "CapabilityFit":
        return CapabilityFit(
            overall=d["overall"],
            reasoning=d["reasoning"],
            coverage_pct=d["coverage_pct"],
            matched_capabilities=d.get("matched_capabilities", []),
            unmatched_capabilities=d.get("unmatched_capabilities", []),
            gaps=[CapabilityGap(**g) for g in d.get("gaps", [])],
        )


def assess_capability_fit(result: Phase1Result, core_capabilities: list[str]) -> CapabilityFit:
    if not result.requirements:
        return CapabilityFit(overall="No-Go", reasoning="No requirements were extracted to assess.",
                              coverage_pct=0.0, unmatched_capabilities=list(core_capabilities))

    capability_keywords = {cap: _capability_keywords(cap) for cap in core_capabilities}

    matched_capability_names: set[str] = set()
    gaps: list[CapabilityGap] = []
    matched_req_count = 0

    for req in result.requirements:
        req_tokens = _tokenize(f"{req.requirement} {req.source_section}")
        hit_any = False
        for cap, cap_tokens in capability_keywords.items():
            if req_tokens & cap_tokens:
                matched_capability_names.add(cap)
                hit_any = True
        if hit_any:
            matched_req_count += 1
        else:
            gaps.append(CapabilityGap(requirement_id=req.id, requirement=req.requirement))

    total = len(result.requirements)
    coverage_pct = round(100.0 * matched_req_count / total, 1) if total else 0.0
    unmatched_capabilities = [c for c in core_capabilities if c not in matched_capability_names]

    if coverage_pct >= 75:
        overall = "Go"
        reasoning = (
            f"{matched_req_count} of {total} requirements ({coverage_pct}%) map directly to a "
            "stated core capability. This RFP is a strong fit — proceed to full proposal."
        )
    elif coverage_pct >= 40:
        overall = "Go, with gaps"
        reasoning = (
            f"Only {matched_req_count} of {total} requirements ({coverage_pct}%) map to a stated "
            "core capability. This is bidable, but the unmatched requirements below should get a "
            "human read before committing — they may need a subcontractor, a scope carve-out, or "
            "an updated capability statement."
        )
    else:
        overall = "No-Go"
        reasoning = (
            f"Only {matched_req_count} of {total} requirements ({coverage_pct}%) map to a stated "
            "core capability. Most of what this RFP is asking for falls outside our documented "
            "service offerings — recommend a leadership bid/no-bid review before investing further "
            "proposal effort."
        )

    return CapabilityFit(
        overall=overall,
        reasoning=reasoning,
        coverage_pct=coverage_pct,
        matched_capabilities=sorted(matched_capability_names),
        unmatched_capabilities=unmatched_capabilities,
        gaps=gaps,
    )
