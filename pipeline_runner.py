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
from pipeline.document_validity import DocumentValidityResult, classify_document_validity
from pipeline.go_no_go import assess_capability_fit
from pipeline.phase1_pipeline import guess_agency, guess_project_title, run_phase1
from pipeline.phase2_pipeline import resolve_with_responses
from pipeline.phase3_pipeline import load_company_profile, run_phase3
from pipeline.phase4_pipeline import ComplianceMatrixIncompleteError, run_phase4
from pipeline.pricing_engine import build_pricing_summary
from pipeline.response_reader import normalize_q_num, read_answer_rows
from pipeline.rfp_reader import read_rfp
from pipeline.schema import Phase1Result
from pipeline.template_mapper import extract_template_headings, map_sections_to_template

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


def check_document_validity(content: bytes, extension: str) -> DocumentValidityResult | None:
    """Synchronous, pre-project-creation bid-document check. Called directly from main.py's
    POST /projects route (not as a background task) so the uploader gets an immediate answer on
    the same page — instead of the old flow, where the check only ran inside the
    process_new_upload background task, so a rejected upload silently sat as a permanent
    'Not a Bid Document' row the user only discovered by revisiting the project or dashboard
    page later.

    Returns None when there's no live LLM client to check with (demo mode with no API key) —
    the caller should then let the upload proceed exactly as it did before this check existed;
    process_new_upload's own demo-case handling governs what happens next in that case."""
    client = _get_client()
    if client is None:
        return None
    rfp_text = _read_upload_via_temp_file(content, extension, read_rfp)
    return classify_document_validity(rfp_text, client)


def process_new_upload(project_id: str, content: bytes, extension: str, skip_validity_check: bool = False):
    """Entry point for a freshly uploaded bid invitation. Runs Phase 1 + the Go/No-Go
    capability check, then either halts for clarification or continues straight through
    Phases 3-4 automatically. Takes raw bytes rather than a path so no plaintext copy of the
    upload is ever written to a persistent location — only a temp file that's deleted before
    this function returns.

    `skip_validity_check` is set by main.py's create_project route when it already ran
    check_document_validity() synchronously before creating this project — avoids paying for a
    second, redundant LLM call here. The reupload route (the one remaining caller that can reach
    this function without a prior synchronous check) leaves it False so its upload still gets
    checked."""
    try:
        db.log_action("pipeline_started", project_id, {"stage": "phase1"})
        rfp_text = _read_upload_via_temp_file(content, extension, read_rfp)
        client = _get_client()

        # Bid-document validity gate: runs before any requirement extraction. Only checked when
        # a real LLM client is configured — DEMO_MODE's bundled sample RFPs are known-good and
        # skip straight to the existing demo-case branch below, exactly as before this gate
        # existed.
        if client is not None and not skip_validity_check:
            validity = classify_document_validity(rfp_text, client)
            db.log_action("document_validity_checked", project_id, validity.to_dict())
            if not validity.is_bid_document:
                _record_invalid_document(project_id, validity)
                return

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


def _record_invalid_document(project_id: str, validity: DocumentValidityResult):
    """The upload doesn't look like a bid/RFP solicitation. This isn't a pipeline failure, so it
    deliberately does NOT set error_message (that field drives the generic 'processing hit an
    error' panel) — it's a valid classification outcome with its own status and its own reason
    field, and the uploaded file itself is left exactly where main.py's create_project route
    already stored it (as a 'bid_invitation' document) so it's still available for audit/review
    even though the pipeline never analyzed it."""
    db.update_project(
        project_id,
        status="Not a Bid Document",
        error_message=None,
        validity_rejection_reason=validity.reasoning,
    )
    db.log_action(
        "document_rejected_invalid", project_id,
        {"reasoning": validity.reasoning, "confidence": validity.confidence},
    )


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
    # build_clarification_doc needs a real filesystem path to write to (python-docx writes to a
    # path, not bytes) — this is pure scratch space: built, immediately read back into memory,
    # and discarded. It was previously written under storage.GENERATED_DIR (a mounted disk on
    # Render); now that the durable copy goes straight to Supabase Storage a few lines down,
    # this scratch file belongs in the system temp dir instead — nothing here needs to survive
    # a restart, so it no longer needs a persistent disk at all. build_clarification_doc also
    # writes a '<tmp_path>.mapping.json' sidecar as a side effect; the mapping is already
    # returned above and persisted to the DB (clarification_mapping_json) below, so the sidecar
    # is redundant — TemporaryDirectory cleanup removes it along with everything else.
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = str(pathlib.Path(tmp_dir) / f"_tmp_{db.new_id()}.docx")
        mapping = build_clarification_doc(result, company["company_name"], tmp_path)
        content = pathlib.Path(tmp_path).read_bytes()
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


def _open_requirement_ids_in_order(project: dict) -> list[str]:
    """The currently-still-ambiguous requirement IDs, in the same relative order they were
    originally asked (i.e. the order they appear in phase1_result_json, which is also the order
    build_clarification_doc numbered them in). Used only as the positional-matching fallback in
    extract_client_responses below."""
    if not project.get("phase1_result_json"):
        return []
    saved = json.loads(project["phase1_result_json"])
    return [r["id"] for r in saved["requirements"] if r["status"] == "ambiguous"]


def extract_client_responses(project_id: str, content: bytes) -> dict[str, str]:
    """Parses an uploaded filled-in clarification doc into {requirement_id: answer_text}. Does
    NOT apply anything to the pipeline — this only extracts, so main.py can use it to pre-fill
    the per-question text boxes on the project page for the user to review (and edit) before
    actually submitting anything. Takes raw bytes instead of a path for the same reason as
    process_new_upload — no persistent plaintext copy of the client's document.

    Matching happens in two passes:
      1. Strict: each row's Q# against the sidecar Q# -> requirement_id mapping saved when the
         original clarification doc was generated (tolerating "1" vs "Q1" vs "1." — see
         response_reader.normalize_q_num).
      2. Positional fallback, only if pass 1 matched nothing at all: the Nth answered row maps
         to the Nth currently-open question, in the order they were originally asked. This
         covers the common real-world case where the answer document isn't the exact file we
         generated — the client (or the bid team) retyped it, renumbered it, or used their own
         format — so the Q# labels don't line up with our internal mapping even though the
         answers are in the same order as the questions. Without this fallback, autofill quietly
         found zero matches for any document that wasn't byte-for-byte our own template."""
    project = db.get_project(project_id)
    mapping = json.loads(project["clarification_mapping_json"]) if project.get("clarification_mapping_json") else {}

    rows = _read_upload_via_temp_file(content, ".docx", read_answer_rows)

    responses: dict[str, str] = {}
    for q_num_raw, _question, answer in rows:
        req_id = mapping.get(q_num_raw) or mapping.get(normalize_q_num(q_num_raw) or "")
        if req_id:
            responses[req_id] = answer

    if not responses and rows:
        open_ids_in_order = _open_requirement_ids_in_order(project)
        for (_, _, answer), req_id in zip(rows, open_ids_in_order):
            responses.setdefault(req_id, answer)
        if responses:
            db.log_action(
                "responses_autofetched_positional_fallback", project_id,
                {"matched_count": len(responses), "row_count": len(rows)},
            )

    return responses


def _apply_client_responses(project_id: str, responses: dict[str, str]):
    """Shared by process_client_responses (legacy docx-upload path, still used for auditing the
    original file) and process_manual_responses (the per-question web form, now the primary way
    to resume): applies client answers to the still-ambiguous requirements, re-checks the
    pipeline gate, and either proceeds to Phase 3/4 or reports back exactly which questions are
    still blocking — see Phase1Result.get_blocking() for what "still blocking" means now that
    escalated items no longer loop forever."""
    project = db.get_project(project_id)
    result = Phase1Result.from_dict(json.loads(project["phase1_result_json"]))
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
        blocking = result.get_blocking()
        db.update_project(project_id, status="Clarifications Sent")
        db.log_action("responses_insufficient", project_id, {"still_blocking": [r.id for r in blocking]})
    else:
        _run_phase3_and_4(project_id, result, client, demo_case="lakeview" if client is None else None)


def process_client_responses(project_id: str, content: bytes):
    """Entry point when the bid team uploads the client's filled-in clarification responses
    directly (bypassing the manual review step) — kept for any caller that still wants the old
    upload-and-immediately-apply behavior. The web app's own upload_responses route no longer
    calls this; it uses extract_client_responses() to pre-fill the review form instead, and
    process_manual_responses() below to actually apply what the user confirmed."""
    try:
        responses = extract_client_responses(project_id, content)
        _apply_client_responses(project_id, responses)
    except Exception as e:  # noqa: BLE001
        _record_failure(project_id, "responses", e)


def process_manual_responses(project_id: str, responses: dict[str, str]):
    """Entry point for the per-question web form on the project page (POST
    /projects/{id}/responses/submit) — the primary way clarification responses are resumed now.
    `responses` is already a plain {requirement_id: answer_text} dict assembled directly from
    the submitted form fields, whether the user typed them by hand or they were auto-filled from
    an uploaded doc via extract_client_responses() and then reviewed."""
    try:
        _apply_client_responses(project_id, responses)
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

    # Phase 3/4 are now done, but the .docx is no longer built here. Instead the generated
    # content is persisted and the project stops for human review: first an assumptions
    # accept/cancel decision (only if the model needed to make any), then an editable preview
    # with a template choice — the docx is only actually built once the user finalizes that
    # preview (see finalize_proposal below).
    db.update_project(project_id, phase4_content_json=json.dumps(
        {k: v for k, v in content.items() if k != "company"}
    ))

    assumption_items = [r for r in result.requirements if r.status == "assumption_needed"]
    if assumption_items:
        db.update_project(project_id, status="Awaiting Assumptions Approval")
        db.log_action("awaiting_assumptions_approval", project_id, {"count": len(assumption_items)})
    else:
        db.update_project(project_id, status="Awaiting Preview")
        db.log_action("awaiting_preview", project_id, {})


def build_preview_document_bytes(content: dict, template_choice: str,
                                  custom_template_bytes: bytes | None) -> bytes:
    """Builds exactly the same document finalize_proposal() would produce — the default fresh
    document, or (for a custom template) the client's own template with matched sections
    inserted in place and any sections it doesn't have appended under a 'Needs Manual
    Placement' heading, via the existing template_mapper machinery — but just returns the bytes
    instead of persisting anything. This is what powers the 'Download Preview Document' action:
    the user can open the actual formatted document (in Word or similar) and review or edit it
    directly, rather than only ever seeing plain web-form fields, before finalizing anything."""
    company = load_company_profile()
    full_content = dict(content)
    full_content["company"] = company

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = str(pathlib.Path(tmp_dir) / f"_tmp_{db.new_id()}.docx")
        if template_choice == "custom" and custom_template_bytes:
            template_path = str(pathlib.Path(tmp_dir) / f"_template_{db.new_id()}.docx")
            pathlib.Path(template_path).write_bytes(custom_template_bytes)
            headings = extract_template_headings(template_path)
            mapping = map_sections_to_template(headings)
            build_final_proposal(tmp_path, full_content, template_path=template_path, section_mapping=mapping)
        else:
            build_final_proposal(tmp_path, full_content)
        return pathlib.Path(tmp_path).read_bytes()


def finalize_proposal(project_id: str, edited_content: dict, template_choice: str,
                       custom_template_bytes: bytes | None, final_document_bytes: bytes | None = None):
    """Runs once the user submits the editable preview (POST /projects/{id}/preview/generate).
    This is the only place the final .docx is actually built now — everything before this point
    (Phase 1-4, the assumptions gate, the preview itself) only ever produced/edited JSON content.
    Real work happens here (docx build + Supabase upload), so this is invoked as a background
    task, unlike the cheap status-flip routes for the assumptions gate.

    `final_document_bytes`, when provided, is the user's own edited copy of the preview document
    (downloaded via build_preview_document_bytes above, edited directly in Word, then re-uploaded
    on the preview form) — in that case it's used as-is as the final proposal, skipping the
    build entirely, since the user already produced the exact final document by hand."""
    try:
        if final_document_bytes is not None:
            proposal_bytes = final_document_bytes
        else:
            proposal_bytes = build_preview_document_bytes(edited_content, template_choice, custom_template_bytes)

        stored_path, encrypted = storage.save_generated(project_id, "final_proposal.docx", proposal_bytes)
        db.add_document(project_id, "final_proposal", "final_proposal.docx", stored_path, encrypted)
        db.update_project(
            project_id,
            status="Ready to Generate",
            phase4_content_json=json.dumps(edited_content),
            template_choice=template_choice,
        )
        db.log_action("final_proposal_generated", project_id, {
            "template_choice": template_choice,
            "used_uploaded_final_document": final_document_bytes is not None,
        })
    except Exception as e:  # noqa: BLE001
        _record_failure(project_id, "finalize_proposal", e, status="Awaiting Preview")


def recompute_staffing_and_pricing(edited_staffing_plan: list[dict]) -> dict:
    """Re-runs the existing, deterministic pricing engine against user-edited headcount/hours
    figures so the final numbers (total hours, subtotal, labor subtotal, contingency, total)
    stay arithmetically consistent no matter what the user changed in the preview — no new
    pricing logic needed, this is exactly what build_pricing_summary already does for the
    original Phase 3 output."""
    return build_pricing_summary(edited_staffing_plan).to_dict()
