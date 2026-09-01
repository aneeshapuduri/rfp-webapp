"""
Orchestrates the Phase 1-4 pipeline for the web app. Runs as a background task per project so
uploads don't block the HTTP request (per the NFR performance spec).

DEMO MODE: this sandbox has no ANTHROPIC_API_KEY by default. To make the whole app testable
end-to-end without one, set DEMO_MODE=true explicitly *and* leave the API key unset — uploads
are then matched against the bundled sample RFPs by content fingerprint and routed through the
same hand-authored demo data used in the Phase 1-4 test suites, exercising the exact same
pipeline code with the LLM call itself swapped for canned output.

Unlike the original version of this file, DEMO_MODE now defaults to OFF (fail-closed). A
production deployment that forgets to set an API key will get a clear, loud error on every
upload instead of silently serving fabricated requirements and pricing through an identical
UI — see the code review that flagged the old default-on behavior as a real risk.
"""
from __future__ import annotations

import json
import logging
import os
import pathlib
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "pipeline"))

import db
import storage
from pipeline.claude_client import ClaudeClient
from pipeline.clarification_doc_builder import build_clarification_doc
from pipeline.document_builder import build_final_proposal
from pipeline.go_no_go import assess_capability_fit
from pipeline.phase1_pipeline import guess_agency, guess_project_title, run_phase1
from pipeline.phase2_pipeline import resolve_with_responses
from pipeline.phase3_pipeline import run_phase3
from pipeline.phase4_pipeline import ComplianceMatrixIncompleteError, run_phase4
from pipeline.response_reader import read_client_responses
from pipeline.rfp_reader import read_rfp
from pipeline.schema import Phase1Result

logger = logging.getLogger("rfp_agent.pipeline")

DEMO_MODE = os.environ.get("DEMO_MODE", "false").lower() == "true"


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


def _no_client_error() -> RuntimeError:
    if DEMO_MODE:
        return RuntimeError(
            "No ANTHROPIC_API_KEY or GEMINI_API_KEY is set, and this document doesn't match a "
            "known DEMO_MODE sample. Set an API key to process real bid invitations."
        )
    return RuntimeError(
        "No ANTHROPIC_API_KEY or GEMINI_API_KEY is configured, so this deployment cannot "
        "process bid invitations. If this is a sandbox/test environment, an administrator can "
        "explicitly set DEMO_MODE=true to enable canned demo output for the bundled sample "
        "RFPs — it is intentionally off by default so a misconfigured production deployment "
        "never silently serves fabricated results."
    )


def _record_failure(project_id: str, stage: str, exc: Exception, status: str | None = None):
    """Logs the full traceback server-side only, and stores a short, safe-to-display message
    on the project row. Previously the full traceback (file paths, library internals, and any
    text embedded in the exception) was rendered verbatim on the project page to anyone who
    could view it — see the code review's 'internal error detail exposed to users' finding."""
    ref = db.new_id()[:8]
    logger.exception("[%s] pipeline failure during %s (ref=%s)", project_id, stage, ref)
    user_message = (
        f"Processing failed during {stage}: {exc}\n"
        f"(ref: {ref} — full details are in the server log, not shown here for security reasons.)"
    )
    fields = {"error_message": user_message}
    if status:
        fields["status"] = status
    db.update_project(project_id, **fields)
    db.log_action("pipeline_error", project_id, {"stage": stage, "ref": ref, "error": str(exc)})


def _read_upload_via_temp_file(content: bytes, extension: str, reader):
    """Writes `content` to a short-lived temp file (needed because some readers — pypdf,
    python-docx via the .txt/.docx/.pdf branches in rfp_reader — expect a path), calls
    `reader(path)`, and unconditionally deletes the temp file afterward. Nothing is ever left
    behind in the app's own data directory the way the old '_working' plaintext copies were."""
    fd, path = tempfile.mkstemp(suffix=extension)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(content)
        return reader(path)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def process_new_upload(project_id: str, content: bytes, extension: str):
    """Entry point for a freshly uploaded bid invitation. Runs Phase 1 + the Go/No-Go
    capability check, then either halts for clarification or continues straight through
    Phases 3-4 automatically. Takes raw bytes rather than a path so no plaintext copy of the
    upload is ever written to a persistent location — only a temp file that's deleted before
    this function returns."""
    try:
        db.log_action("pipeline_started", project_id, {"stage": "phase1"})
        rfp_text = _read_upload_via_temp_file(content, extension, read_rfp)
        client = _get_client()

        if client is None and DEMO_MODE:
            case = _detect_demo_case(rfp_text)
            if case is None:
                raise _no_client_error()
            from pipeline.demo_data import LAKEVIEW_ITEMS, NORTHFIELD_ITEMS
            from pipeline.demo_data_cedarvalley import CEDARVALLEY_ITEMS
            demo_map = {"lakeview": LAKEVIEW_ITEMS, "northfield": NORTHFIELD_ITEMS, "cedarvalley": CEDARVALLEY_ITEMS}
            raw_items = demo_map[case]
            result = Phase1Result.from_raw(guess_project_title(rfp_text), guess_agency(rfp_text), raw_items)
        elif client is None:
            raise _no_client_error()
        else:
            result = run_phase1(rfp_text, client)

        db.update_project(
            project_id,
            agency=result.agency,
            phase1_result_json=json.dumps(result.to_dict()),
        )
        db.log_action("phase1_complete", project_id, result.summary)

        _run_capability_fit(project_id, result)

        if result.pipeline_decision == "halt_for_clarification":
            _generate_clarification_doc(project_id, result)
        else:
            _run_phase3_and_4(project_id, result, client, demo_case=_detect_demo_case(rfp_text) if client is None else None)

    except Exception as e:  # noqa: BLE001
        _record_failure(project_id, "phase1", e, status="Analyzing")


def _run_capability_fit(project_id: str, result: Phase1Result):
    """Go/No-Go: score extracted requirements against config/company_profile.json's stated
    core capabilities. Deliberately never fatal to the pipeline — a bug in this heuristic
    should not block a real proposal from being generated, so failures are logged and
    swallowed rather than surfaced as a pipeline error."""
    try:
        company = json.loads(_config_path("company_profile.json").read_text())
        fit = assess_capability_fit(result, company.get("core_capabilities", []))
        db.update_project(project_id, capability_fit_json=json.dumps(fit.to_dict()))
        db.log_action("capability_fit_assessed", project_id, {"overall": fit.overall, "coverage_pct": fit.coverage_pct})
    except Exception:  # noqa: BLE001
        logger.exception("[%s] capability fit assessment failed (non-fatal)", project_id)


def _generate_clarification_doc(project_id: str, result: Phase1Result):
    company = json.loads((_config_path("company_profile.json")).read_text())
    out_dir = storage.GENERATED_DIR / project_id
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = str(out_dir / f"_tmp_{db.new_id()}.docx")
    mapping = build_clarification_doc(result, company["company_name"], tmp_path)
    # build_clarification_doc also writes a '<tmp_path>.mapping.json' sidecar as a side effect.
    # The mapping is already returned above and persisted to the DB (clarification_mapping_json)
    # below, so the sidecar file itself is redundant — delete it rather than leaving it on disk.
    mapping_sidecar = pathlib.Path(tmp_path).with_suffix(".mapping.json")

    content = open(tmp_path, "rb").read()
    os.unlink(tmp_path)
    if mapping_sidecar.exists():
        mapping_sidecar.unlink()
    stored_path, encrypted = storage.save_generated(project_id, "clarification_questions.docx", content)
    db.add_document(project_id, "clarification_questions", "clarification_questions.docx", stored_path, encrypted)
    db.update_project(
        project_id,
        status="Clarifications Sent",
        clarification_mapping_json=json.dumps(mapping),
    )
    db.log_action("clarification_doc_generated", project_id, {"question_count": len(mapping)})


def _config_path(name: str):
    return pathlib.Path(__file__).resolve().parent / "config" / name


def process_client_responses(project_id: str, content: bytes):
    """Entry point when the bid team uploads the client's filled-in clarification responses.
    Takes raw bytes (always a .docx) instead of a path for the same reason as
    process_new_upload — no persistent plaintext copy."""
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

        mapping = json.loads(project["clarification_mapping_json"])

        def _read_responses(path):
            # The mapping file is small, app-generated JSON — write/delete it alongside the
            # temp docx rather than leaving a copy in the generated/ directory permanently.
            fd, mapping_path = tempfile.mkstemp(suffix=".json")
            try:
                with os.fdopen(fd, "w") as f:
                    json.dump(mapping, f)
                return read_client_responses(path, mapping_path)
            finally:
                try:
                    os.unlink(mapping_path)
                except OSError:
                    pass

        responses = _read_upload_via_temp_file(content, ".docx", _read_responses)
        db.log_action("client_responses_received", project_id, {"answered_count": len(responses)})

        client = _get_client()
        if client is None and DEMO_MODE:
            from pipeline.test_phase2 import DEMO_RESOLUTIONS
            result = resolve_with_responses(result, responses, client=None, demo_resolutions=DEMO_RESOLUTIONS)
        elif client is None:
            raise _no_client_error()
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
        _record_failure(project_id, "responses", e)


def _run_phase3_and_4(project_id: str, result: Phase1Result, client: ClaudeClient | None, demo_case: str | None):
    db.log_action("pipeline_started", project_id, {"stage": "phase3"})
    project = db.get_project(project_id)
    duration_months = project.get("duration_months") or 9

    if client is None and demo_case:
        from pipeline.demo_data_phase3 import LAKEVIEW_STAFFING_PLAN, LAKEVIEW_TECH_APPROACH
        phase3 = run_phase3(result, duration_months, demo_technology_approach=LAKEVIEW_TECH_APPROACH,
                             demo_staffing_plan=LAKEVIEW_STAFFING_PLAN)
    elif client is None:
        raise _no_client_error()
    else:
        phase3 = run_phase3(result, duration_months, client=client)

    db.update_project(project_id, phase3_result_json=json.dumps(phase3.to_dict()))
    db.log_action("phase3_complete", project_id, {"total_price": phase3.pricing["total"]})

    db.log_action("pipeline_started", project_id, {"stage": "phase4"})
    if client is None and demo_case:
        from pipeline.narrative_demo_data import DEMO_NARRATIVE
        content = run_phase4(result, phase3, duration_months, demo_narrative=DEMO_NARRATIVE)
    elif client is None:
        raise _no_client_error()
    else:
        content = run_phase4(result, phase3, duration_months, client=client)

    out_dir = storage.GENERATED_DIR / project_id
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = str(out_dir / f"_tmp_{db.new_id()}.docx")
    build_final_proposal(tmp_path, content)
    proposal_bytes = open(tmp_path, "rb").read()
    os.unlink(tmp_path)

    stored_path, encrypted = storage.save_generated(project_id, "final_proposal.docx", proposal_bytes)
    db.add_document(project_id, "final_proposal", "final_proposal.docx", stored_path, encrypted)
    db.update_project(project_id, status="Ready to Generate", phase4_content_json=json.dumps(
        {k: v for k, v in content.items() if k != "company"}
    ))
    db.log_action("final_proposal_generated", project_id, {"total_price": phase3.pricing["total"]})
