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

# Common word-form mismatches (an RFP saying "develop"/"engage"/"integrate" against a capability
# statement saying "development"/"engagement"/"integrations") were a real source of false "gap"
# flags — the exact-token match never had a chance even though the two phrasings mean the same
# thing. This is a deliberately conservative suffix-stripping normalizer, not a full stemmer:
# only plain, low-risk English inflections (plurals, gerunds) are stripped, in priority order so
# a word doesn't get double-stripped. Irregular pairs (e.g. "maintain" vs "maintenance") aren't
# unifiable this way — those are handled instead by spelling out both forms directly in
# config/company_profile.json's core_capabilities text, which stays the source of truth for what
# the company actually offers.
def _stem(tok: str) -> str:
    if tok.endswith("ing") and len(tok) > 5:
        return tok[:-3]
    if tok.endswith(("ches", "shes", "xes", "zes", "sses")) and len(tok) > 5:
        return tok[:-2]
    if tok.endswith("ies") and len(tok) > 5:
        return tok[:-3] + "y"
    if tok.endswith("s") and not tok.endswith("ss") and len(tok) > 4:
        return tok[:-1]
    return tok


def _tokenize(text: str) -> set[str]:
    tokens = set()
    for raw in _TOKEN_RE.findall(text or ""):
        tok = _stem(raw.strip(".").lower())
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
class RequirementCapabilityMatch:
    """Per-requirement result of the same keyword-overlap check assess_capability_fit runs to
    build the aggregate CapabilityFit — one row per extracted requirement/ask, so the Summary
    tab can show "Available in Pamten: Yes/No" against every single item from the RFP, not just
    the aggregate coverage percentage and the (unmatched-only) gaps list below. `available` is
    exactly the inverse of "this requirement's id appears in `gaps`" — kept as an explicit,
    readable field rather than making the template re-derive it from the gaps list.

    `manually_marked_available` is True when an admin overrode this specific item (see
    apply_overrides below) rather than the deterministic keyword check finding a match — kept
    separate from `available` so a template can still badge these as "marked available" instead
    of showing them identically to an automatic match."""
    requirement_id: str
    requirement: str
    available: bool
    matched_capabilities: list[str] = field(default_factory=list)
    manually_marked_available: bool = False

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
    # Every extracted requirement, matched or not — see RequirementCapabilityMatch above.
    # Optional/empty on data written before this field existed (from_dict defaults it to []).
    per_requirement: list[RequirementCapabilityMatch] = field(default_factory=list)
    # Items an admin manually marked available via apply_overrides — a subset of what the
    # deterministic check originally flagged as a gap. Empty unless apply_overrides ran.
    overridden_items: list[CapabilityGap] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "overall": self.overall,
            "reasoning": self.reasoning,
            "coverage_pct": self.coverage_pct,
            "matched_capabilities": self.matched_capabilities,
            "unmatched_capabilities": self.unmatched_capabilities,
            "gaps": [g.to_dict() for g in self.gaps],
            "per_requirement": [r.to_dict() for r in self.per_requirement],
            "overridden_items": [g.to_dict() for g in self.overridden_items],
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
            per_requirement=[RequirementCapabilityMatch(**r) for r in d.get("per_requirement", [])],
            overridden_items=[CapabilityGap(**g) for g in d.get("overridden_items", [])],
        )


def _classify(matched_req_count: int, total: int) -> tuple[str, str, float]:
    """Shared by assess_capability_fit (the original deterministic read) and apply_overrides
    (the same read recomputed after an admin manually marks some gaps available) so the
    Go/"Go, with gaps"/No-Go thresholds and their wording never drift apart between the two."""
    coverage_pct = round(100.0 * matched_req_count / total, 1) if total else 0.0
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
    return overall, reasoning, coverage_pct


def assess_capability_fit(result: Phase1Result, core_capabilities: list[str]) -> CapabilityFit:
    if not result.requirements:
        return CapabilityFit(overall="No-Go", reasoning="No requirements were extracted to assess.",
                              coverage_pct=0.0, unmatched_capabilities=list(core_capabilities))

    capability_keywords = {cap: _capability_keywords(cap) for cap in core_capabilities}

    matched_capability_names: set[str] = set()
    gaps: list[CapabilityGap] = []
    per_requirement: list[RequirementCapabilityMatch] = []
    matched_req_count = 0

    for req in result.requirements:
        req_tokens = _tokenize(f"{req.requirement} {req.source_section}")
        hit_any = False
        req_matched_caps: list[str] = []
        for cap, cap_tokens in capability_keywords.items():
            if req_tokens & cap_tokens:
                matched_capability_names.add(cap)
                req_matched_caps.append(cap)
                hit_any = True
        if hit_any:
            matched_req_count += 1
        else:
            gaps.append(CapabilityGap(requirement_id=req.id, requirement=req.requirement))
        per_requirement.append(RequirementCapabilityMatch(
            requirement_id=req.id, requirement=req.requirement,
            available=hit_any, matched_capabilities=req_matched_caps,
        ))

    total = len(result.requirements)
    unmatched_capabilities = [c for c in core_capabilities if c not in matched_capability_names]
    overall, reasoning, coverage_pct = _classify(matched_req_count, total)

    return CapabilityFit(
        overall=overall,
        reasoning=reasoning,
        coverage_pct=coverage_pct,
        matched_capabilities=sorted(matched_capability_names),
        unmatched_capabilities=unmatched_capabilities,
        gaps=gaps,
        per_requirement=per_requirement,
    )


def apply_overrides(fit: CapabilityFit, overridden_requirement_ids: set[str]) -> CapabilityFit:
    """Layers admin-recorded "we can actually do this" overrides on top of a deterministic
    CapabilityFit, without mutating the original assessment — capability_fit_json keeps the raw,
    keyword-only read as a stable historical record; this returns a fresh CapabilityFit for
    display, recomputed with the exact same coverage-threshold logic assess_capability_fit uses
    (via _classify) so the overall read and coverage percentage stay internally consistent once
    some gaps are marked available. `matched_capabilities`/`unmatched_capabilities` are left as
    the deterministic keyword check found them — overriding one requirement doesn't necessarily
    mean an entire stated capability now matches, so that comparison stays untouched.

    If `overridden_requirement_ids` is empty this returns `fit` unchanged (same object) so
    callers can call this unconditionally without a branch for "no overrides recorded yet"."""
    if not overridden_requirement_ids:
        return fit

    if not fit.per_requirement:
        # Legacy capability_fit_json predating the per_requirement field (see its docstring
        # above) has no per-item availability data to safely recompute a total/coverage_pct
        # from, so this only narrows the gaps list rather than risk fabricating a percentage
        # off an incomplete denominator — still enough to unblock an admin on an old project.
        overridden_items = [g for g in fit.gaps if g.requirement_id in overridden_requirement_ids]
        if not overridden_items:
            return fit
        remaining_gaps = [g for g in fit.gaps if g.requirement_id not in overridden_requirement_ids]
        return CapabilityFit(
            overall=fit.overall, reasoning=fit.reasoning, coverage_pct=fit.coverage_pct,
            matched_capabilities=fit.matched_capabilities, unmatched_capabilities=fit.unmatched_capabilities,
            gaps=remaining_gaps, per_requirement=[], overridden_items=overridden_items,
        )

    per_requirement: list[RequirementCapabilityMatch] = []
    gaps: list[CapabilityGap] = []
    overridden_items: list[CapabilityGap] = []
    matched_req_count = 0

    for r in fit.per_requirement:
        is_override = r.requirement_id in overridden_requirement_ids and not r.available
        available = r.available or is_override
        if available:
            matched_req_count += 1
        per_requirement.append(RequirementCapabilityMatch(
            requirement_id=r.requirement_id, requirement=r.requirement,
            available=available, matched_capabilities=r.matched_capabilities,
            manually_marked_available=is_override or r.manually_marked_available,
        ))
        if not available:
            gaps.append(CapabilityGap(requirement_id=r.requirement_id, requirement=r.requirement))
        elif is_override or r.manually_marked_available:
            overridden_items.append(CapabilityGap(requirement_id=r.requirement_id, requirement=r.requirement))

    total = len(per_requirement)
    overall, reasoning, coverage_pct = _classify(matched_req_count, total)
    if overridden_items:
        reasoning += (
            f" ({len(overridden_items)} item{'s' if len(overridden_items) != 1 else ''} manually "
            "marked available by an admin, on top of the automatic keyword check.)"
        )

    return CapabilityFit(
        overall=overall,
        reasoning=reasoning,
        coverage_pct=coverage_pct,
        matched_capabilities=fit.matched_capabilities,
        unmatched_capabilities=fit.unmatched_capabilities,
        gaps=gaps,
        per_requirement=per_requirement,
        overridden_items=overridden_items,
    )
