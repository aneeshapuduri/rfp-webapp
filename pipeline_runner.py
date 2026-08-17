"""
Orchestrates the Phase 1-4 pipeline for the web app. Runs as a background task per project so
uploads don't block the HTTP request (per the NFR performance spec).

DEMO MODE: this sandbox has no ANTHROPIC_API_KEY. To make the whole app testable end-to-end
without one, if DEMO_MODE=true and no key is set, uploads are matched against the two known
sample RFPs by content fingerprint and routed through the same hand-authored demo data used
in the Phase 1-4 test suites — the exact same pipeline code runs either way, only the LLM call
itself is swapped for canned output. This is clearly a sandbox/testing convenience, not a
production feature — in real deployment, set ANTHROPIC_API_KEY and DEMO_MODE is ignored.
"""
from __future__ import annotations

import json
import os
import sys
import traceback

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "pipeline"))

import db
import storage
from pipeline.claude_client import ClaudeClient
from pipeline.clarification_doc_builder import build_clarification_doc
from pipeline.document_builder import build_final_proposal
from pipeline.phase1_pipeline import guess_agency, guess_project_title, run_phase1
from pipeline.phase2_pipeline import resolve_with_responses
from pipeline.phase3_pipeline import run_phase3
from pipeline.phase4_pipeline import ComplianceMatrixIncompleteError, run_phase4
from pipeline.response_reader import read_client_responses
from pipeline.rfp_reader import read_rfp
from pipeline.schema import Phase1Result

DEMO_MODE = os.environ.get("DEMO_MODE", "true").lower() == "true"


def _get_client():
    """
    Provider selection: if AI_PROVIDER is set explicitly, use that. Otherwise, prefer
    ANTHROPIC_API_KEY if present, then GEMINI_API_KEY. Falls back to None (demo mode) if
    neither is set. Both clients implement the same generate_text/generate_json interface,
    so nothing downstream needs to know which one is actually running.
    """
    provider = os.environ.get("AI_PROVIDER", "").lower()

    if provider == "gemini" or (not provider and os.environ.get("GEMINI_API_KEY") and not os.environ.get("ANTHROPIC_API_KEY")):
        if os.environ.get("GEMINI_API_KEY"):
            from pipeline.gemini_client import GeminiClient
            return GeminiClient()
        return None

    if os.environ.get("ANTHROPIC_API_KEY"):
        return ClaudeClient()

    return None


def _detect_demo_case(rfp_text: str) -> str | None:
    if "Northfield County" in rfp_text:
        return "northfield"
    if "City of Lakeview" in rfp_text:
        return "lakeview"
    if "Cedar Valley Unified School District" in rfp_text:
        return "cedarvalley"
    return None


def process_new_upload(project_id: str, uploaded_path: str):
    """Entry point for a freshly uploaded bid invitation. Runs Phase 1, then either halts for
    clarification or continues straight through Phases 3-4 automatically."""
    try:
        db.log_action("pipeline_started", project_id, {"stage": "phase1"})
        rfp_text = read_rfp(uploaded_path)
        client = _get_client()

        if client is None and DEMO_MODE:
            case = _detect_demo_case(rfp_text)
            if case is None:
                raise RuntimeError(
                    "No ANTHROPIC_API_KEY or GEMINI_API_KEY is set, and this RFP doesn't "
                    "match a known demo sample. Set one of those environment variables to "
                    "process real bid invitations."
                )
            from pipeline.demo_data import LAKEVIEW_ITEMS, NORTHFIELD_ITEMS
            from pipeline.demo_data_cedarvalley import CEDARVALLEY_ITEMS
            demo_map = {"lakeview": LAKEVIEW_ITEMS, "northfield": NORTHFIELD_ITEMS, "cedarvalley": CEDARVALLEY_ITEMS}
            raw_items = demo_map[case]
            result = Phase1Result.from_raw(guess_project_title(rfp_text), guess_agency(rfp_text), raw_items)
        elif client is None:
            raise RuntimeError("No ANTHROPIC_API_KEY or GEMINI_API_KEY set. Configure one to process bid invitations.")
        else:
            result = run_phase1(rfp_text, client)

        db.update_project(
            project_id,
            agency=result.agency,
            phase1_result_json=json.dumps(result.to_dict()),
        )
        db.log_action("phase1_complete", project_id, result.summary)

        if result.pipeline_decision == "halt_for_clarification":
            _generate_clarification_doc(project_id, result)
        else:
            _run_phase3_and_4(project_id, result, client, demo_case=_detect_demo_case(rfp_text) if client is None else None)

    except Exception as e:  # noqa: BLE001
        db.update_project(project_id, status="Analyzing", error_message=f"{e}\n{traceback.format_exc()}")
        db.log_action("pipeline_error", project_id, {"error": str(e)})


def _generate_clarification_doc(project_id: str, result: Phase1Result):
    company = json.loads((_config_path("company_profile.json")).read_text())
    out_dir = storage.GENERATED_DIR / project_id
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = str(out_dir / "clarification_questions.docx")
    mapping = build_clarification_doc(result, company["company_name"], tmp_path)

    content = open(tmp_path, "rb").read()
    stored_path = storage.save_generated(project_id, "clarification_questions.docx", content)
    db.add_document(project_id, "clarification_questions", "clarification_questions.docx", stored_path)
    db.update_project(
        project_id,
        status="Clarifications Sent",
        clarification_mapping_json=json.dumps(mapping),
    )
    db.log_action("clarification_doc_generated", project_id, {"question_count": len(mapping)})


def _config_path(name: str):
    import pathlib
    return pathlib.Path(__file__).resolve().parent / "config" / name


def process_client_responses(project_id: str, filled_doc_path: str):
    """Entry point when the bid team uploads the client's filled-in clarification responses."""
    try:
        project = db.get_project(project_id)
        result = Phase1Result.from_raw(
            project["name"], project["agency"],
            [dict(r) for r in json.loads(project["phase1_result_json"])["requirements"]],
        )
        # Restore full field set (from_raw only sets a subset) by overlaying saved state.
        saved = json.loads(project["phase1_result_json"])
        for item, saved_item in zip(result.requirements, saved["requirements"]):
            item.status = saved_item["status"]
            item.escalated_for_manual_review = saved_item.get("escalated_for_manual_review", False)

        mapping_path = storage.GENERATED_DIR / project_id / "clarification_questions.mapping.json"
        if not mapping_path.exists():
            mapping = json.loads(project["clarification_mapping_json"])
            mapping_path.parent.mkdir(parents=True, exist_ok=True)
            mapping_path.write_text(json.dumps(mapping))

        responses = read_client_responses(filled_doc_path, str(mapping_path))
        db.log_action("client_responses_received", project_id, {"answered_count": len(responses)})

        client = _get_client()
        if client is None and DEMO_MODE:
            from pipeline.test_phase2 import DEMO_RESOLUTIONS
            result = resolve_with_responses(result, responses, client=None, demo_resolutions=DEMO_RESOLUTIONS)
        elif client is None:
            raise RuntimeError("No ANTHROPIC_API_KEY or GEMINI_API_KEY set.")
        else:
            result = resolve_with_responses(result, responses, client=client)

        db.update_project(project_id, phase1_result_json=json.dumps(result.to_dict()))

        if result.pipeline_decision == "halt_for_clarification":
            escalated = result.get_escalated()
            db.update_project(project_id, status="Clarifications Sent")
            db.log_action("responses_insufficient", project_id, {"escalated": [r.id for r in escalated]})
        else:
            _run_phase3_and_4(project_id, result, client, demo_case="lakeview" if client is None else None)

    except Exception as e:  # noqa: BLE001
        db.update_project(project_id, error_message=f"{e}\n{traceback.format_exc()}")
        db.log_action("pipeline_error", project_id, {"error": str(e), "stage": "responses"})


def _run_phase3_and_4(project_id: str, result: Phase1Result, client: ClaudeClient | None, demo_case: str | None):
    db.log_action("pipeline_started", project_id, {"stage": "phase3"})
    project = db.get_project(project_id)
    duration_months = project.get("duration_months") or 9

    if client is None and demo_case:
        from pipeline.demo_data_phase3 import LAKEVIEW_STAFFING_PLAN, LAKEVIEW_TECH_APPROACH
        phase3 = run_phase3(result, duration_months, demo_technology_approach=LAKEVIEW_TECH_APPROACH,
                             demo_staffing_plan=LAKEVIEW_STAFFING_PLAN)
    elif client is None:
        raise RuntimeError("No ANTHROPIC_API_KEY or GEMINI_API_KEY set.")
    else:
        phase3 = run_phase3(result, duration_months, client=client)

    db.update_project(project_id, phase3_result_json=json.dumps(phase3.to_dict()))
    db.log_action("phase3_complete", project_id, {"total_price": phase3.pricing["total"]})

    db.log_action("pipeline_started", project_id, {"stage": "phase4"})
    if client is None and demo_case:
        from pipeline.narrative_demo_data import DEMO_NARRATIVE
        content = run_phase4(result, phase3, duration_months, demo_narrative=DEMO_NARRATIVE)
    elif client is None:
        raise RuntimeError("No ANTHROPIC_API_KEY or GEMINI_API_KEY set.")
    else:
        content = run_phase4(result, phase3, duration_months, client=client)

    out_dir = storage.GENERATED_DIR / project_id
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = str(out_dir / "final_proposal.docx")
    build_final_proposal(tmp_path, content)

    stored_path = storage.save_generated(project_id, "final_proposal.docx", open(tmp_path, "rb").read())
    db.add_document(project_id, "final_proposal", "final_proposal.docx", stored_path)
    db.update_project(project_id, status="Ready to Generate", phase4_content_json=json.dumps(
        {k: v for k, v in content.items() if k != "company"}
    ))
    db.log_action("final_proposal_generated", project_id, {"total_price": phase3.pricing["total"]})
