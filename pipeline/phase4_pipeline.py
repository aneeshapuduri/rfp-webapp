"""
Phase 4: Final Document Assembly.

Generates the remaining narrative sections and the compliance matrix, then hands everything
to document_builder.py. The compliance matrix completeness check is a hard gate: if the model
omits even one requirement, this raises rather than silently shipping an incomplete matrix —
per your stated priority that compliance-matrix accuracy matters most.
"""
from __future__ import annotations

from claude_client import ClaudeClient
from narrative_prompts import (
    closing_prompt,
    compliance_matrix_prompt,
    executive_summary_prompt,
    out_of_scope_prompt,
    past_performance_prompt,
    project_deliverables_prompt,
    project_dependencies_prompt,
    project_objectives_prompt,
    scope_of_services_prompt,
    service_boundaries_prompt,
    staffing_readiness_prompt,
    technical_assumptions_prompt,
    timeline_prompt,
    understanding_prompt,
)
from phase3_pipeline import load_company_profile
from schema import Phase1Result, Phase3Result

# Fallback text used when a project-specific narrative field is missing from a demo/test
# fixture (older demo_narrative dicts built before these fields existed). Keeps every demo
# fixture working without requiring every test to be updated.
_DEMO_FALLBACKS = {
    "scope_of_services": "- Scope of services to be finalized based on the confirmed requirements.",
    "out_of_scope_of_services": "- Items outside the confirmed requirements are considered out of scope.",
    "project_objectives": "- Deliver the confirmed requirements on schedule and within budget.",
    "project_deliverables": "- A deployed, working system meeting the confirmed requirements.",
    "staffing_readiness": "The proposed team mobilizes within two weeks of contract award, "
        "covering kickoff, access provisioning, and environment setup.",
    "technical_assumptions": "- Standard technical assumptions apply unless otherwise noted.",
    "project_dependencies": "- Timely client stakeholder availability and access provisioning.",
    "service_boundaries": "Support is bounded by the terms of the executed agreement and the "
        "confirmed requirements above.",
}


class ComplianceMatrixIncompleteError(Exception):
    pass


def build_compliance_matrix(
    result: Phase1Result,
    company: dict,
    client: ClaudeClient | None = None,
    demo_matrix: list[dict] | None = None,
) -> list[dict]:
    """
    Generates the compliance matrix and enforces 100% coverage of finalized requirements
    (status in clear/assumption_needed — ambiguous items shouldn't exist by Phase 4, since
    the gate would have halted the pipeline before this runs).
    """
    finalized = [r for r in result.requirements if r.status in ("clear", "assumption_needed")]
    required_ids = {r.id for r in finalized}

    if demo_matrix is not None:
        raw_matrix = demo_matrix
    else:
        if client is None:
            raise RuntimeError("No Claude client and no demo_matrix supplied.")
        req_dicts = [{"id": r.id, "requirement": r.requirement} for r in finalized]
        sys_p, user_p = compliance_matrix_prompt(req_dicts, company["core_capabilities"])
        raw_matrix = client.generate_json(sys_p, user_p, max_tokens=3000)

    returned_ids = {item["requirement_id"] for item in raw_matrix}
    missing = required_ids - returned_ids
    if missing:
        missing_text = [f"{rid}: {result.by_id(rid).requirement}" for rid in missing]
        raise ComplianceMatrixIncompleteError(
            "Compliance matrix is missing requirement(s) — refusing to assemble an incomplete "
            "document. Missing:\n" + "\n".join(f"  - {m}" for m in missing_text)
        )

    extra = returned_ids - required_ids
    if extra:
        raise ComplianceMatrixIncompleteError(
            f"Compliance matrix references unknown requirement ID(s) not in the finalized "
            f"requirement list: {extra}"
        )

    # Attach the actual requirement text so the document builder doesn't need a second lookup.
    by_id = {item["requirement_id"]: item for item in raw_matrix}
    matrix = []
    for r in finalized:
        entry = by_id[r.id]
        matrix.append({
            "requirement_id": r.id,
            "requirement": r.requirement,
            "response": entry["response"],
            "status": entry["status"],
        })
    return matrix


def run_phase4(
    result: Phase1Result,
    phase3: Phase3Result,
    duration_months: float,
    client: ClaudeClient | None = None,
    demo_narrative: dict | None = None,
) -> dict:
    """
    demo_narrative, if supplied, should have keys: executive_summary, understanding, timeline,
    past_performance, closing, compliance_matrix — used by tests instead of live API calls.
    """
    company = load_company_profile()
    clear_requirements = [r.requirement for r in result.requirements if r.status == "clear"]

    if demo_narrative is not None:
        sections = dict(demo_narrative)
    else:
        if client is None:
            raise RuntimeError("No Claude client and no demo_narrative supplied.")
        sections = {}
        sys_p, user_p = executive_summary_prompt(result.project_title, result.agency, clear_requirements, company)
        sections["executive_summary"] = client.generate_text(sys_p, user_p)

        sys_p, user_p = understanding_prompt(result.project_title, result.agency, clear_requirements)
        sections["understanding"] = client.generate_text(sys_p, user_p)

        sys_p, user_p = timeline_prompt(clear_requirements, duration_months)
        sections["timeline"] = client.generate_json(sys_p, user_p)

        sys_p, user_p = past_performance_prompt(clear_requirements, company["past_performance"])
        sections["past_performance"] = client.generate_text(sys_p, user_p)

        sys_p, user_p = closing_prompt(result.project_title, result.agency, company["contact"])
        sections["closing"] = client.generate_text(sys_p, user_p)

        sys_p, user_p = scope_of_services_prompt(clear_requirements)
        sections["scope_of_services"] = client.generate_text(sys_p, user_p)

        sys_p, user_p = out_of_scope_prompt(clear_requirements)
        sections["out_of_scope_of_services"] = client.generate_text(sys_p, user_p)

        sys_p, user_p = project_objectives_prompt(result.project_title, result.agency, clear_requirements)
        sections["project_objectives"] = client.generate_text(sys_p, user_p)

        sys_p, user_p = project_deliverables_prompt(clear_requirements)
        sections["project_deliverables"] = client.generate_text(sys_p, user_p)

        sys_p, user_p = staffing_readiness_prompt(clear_requirements, duration_months)
        sections["staffing_readiness"] = client.generate_text(sys_p, user_p)

        sys_p, user_p = technical_assumptions_prompt(clear_requirements)
        sections["technical_assumptions"] = client.generate_text(sys_p, user_p)

        sys_p, user_p = project_dependencies_prompt(clear_requirements)
        sections["project_dependencies"] = client.generate_text(sys_p, user_p)

        sys_p, user_p = service_boundaries_prompt(clear_requirements)
        sections["service_boundaries"] = client.generate_text(sys_p, user_p)

        sections["compliance_matrix"] = build_compliance_matrix(result, company, client=client)

    # Compliance matrix completeness is enforced even in demo mode, using the same code path.
    if "compliance_matrix" in (demo_narrative or {}):
        required_ids = {r.id for r in result.requirements if r.status in ("clear", "assumption_needed")}
        returned_ids = {item["requirement_id"] for item in sections["compliance_matrix"]}
        missing = required_ids - returned_ids
        if missing:
            raise ComplianceMatrixIncompleteError(f"Demo compliance matrix is missing: {missing}")
        # Attach requirement text (same enrichment build_compliance_matrix does for live calls)
        by_id = {item["requirement_id"]: item for item in sections["compliance_matrix"]}
        enriched = []
        for r in result.requirements:
            if r.id in by_id:
                entry = by_id[r.id]
                enriched.append({
                    "requirement_id": r.id,
                    "requirement": r.requirement,
                    "response": entry["response"],
                    "status": entry["status"],
                })
        sections["compliance_matrix"] = enriched

    return {
        "project_title": result.project_title,
        "agency": result.agency,
        "executive_summary": sections["executive_summary"],
        "understanding": sections["understanding"],
        "technology_approach": phase3.technology_approach,
        "assumptions": phase3.assumptions,
        "timeline": sections["timeline"],
        "staffing": phase3.pricing["lines"],
        "pricing": phase3.pricing,
        "past_performance": sections["past_performance"],
        "compliance_matrix": sections["compliance_matrix"],
        "closing": sections["closing"],
        "scope_of_services": sections.get("scope_of_services", _DEMO_FALLBACKS["scope_of_services"]),
        "out_of_scope_of_services": sections.get(
            "out_of_scope_of_services", _DEMO_FALLBACKS["out_of_scope_of_services"]
        ),
        "project_objectives": sections.get("project_objectives", _DEMO_FALLBACKS["project_objectives"]),
        "project_deliverables": sections.get("project_deliverables", _DEMO_FALLBACKS["project_deliverables"]),
        "staffing_readiness": sections.get("staffing_readiness", _DEMO_FALLBACKS["staffing_readiness"]),
        "technical_assumptions": sections.get("technical_assumptions", _DEMO_FALLBACKS["technical_assumptions"]),
        "project_dependencies": sections.get("project_dependencies", _DEMO_FALLBACKS["project_dependencies"]),
        "service_boundaries": sections.get("service_boundaries", _DEMO_FALLBACKS["service_boundaries"]),
        "company": company,
    }
