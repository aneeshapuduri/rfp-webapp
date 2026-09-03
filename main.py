"""
RFP Proposal Agent — web application.

Routes:
  GET  /home                          personalized welcome page (real project/activity counts)
  GET  /                              dashboard (project list)
  GET  /login, POST /login            sign in
  POST /logout                        sign out
  GET  /new                           upload form
  POST /projects                      validates the file is a bid document synchronously (no
                                       project row is created for a rejected file — the New
                                       Project page just re-renders with an inline error), then
                                       creates the project + kicks off the background pipeline
  GET  /projects/{id}                 project detail view
  POST /projects/{id}/responses       upload a filled-in clarification doc to auto-fill the
                                       per-question response boxes below (does not apply anything
                                       by itself — review/edit, then submit)
  POST /projects/{id}/responses/submit  apply whatever's in the per-question response boxes
                                       (typed by hand and/or auto-filled) and resume the pipeline
  GET  /projects/{id}/documents/{did} download a document
  POST /projects/{id}/reupload        re-upload after a 'Not a Bid Document' halt — kept as a
                                       defensive fallback, but currently unreachable in practice:
                                       since POST /projects now rejects invalid documents before
                                       a project is ever created, no project can enter the
                                       'Not a Bid Document' status this route requires
  POST /projects/{id}/assumptions/accept  accept AI assumptions — only from 'Awaiting Assumptions Approval'
  POST /projects/{id}/assumptions/cancel  cancel the bid over the assumptions made — same gate
  POST /projects/{id}/preview/generate    submit the edited preview + template choice, builds the docx
  POST /projects/{id}/approve         mark Submitted — only valid from 'Ready to Generate'
  POST /projects/{id}/reject          mark Declined — only valid from 'Ready to Generate'
  POST /projects/{id}/delete          soft-delete a project
  GET  /audit                         audit log view (filtered to accessible projects for members)
  GET  /admin/users, POST /admin/users, POST /admin/users/{id}/deactivate,
  POST /admin/users/{id}/access       admin-only user management + per-project access grants

Every route above except /login and /static/* requires a logged-in, active user — enforced by
auth.AuthGateMiddleware, not by a per-route check, so a route added later can't accidentally
ship without one. Every POST route also requires a valid CSRF token (auth.verify_csrf).

Per-project access: admins always have full access to every project. Members only have access
to projects an admin has explicitly granted them (db.project_access) plus projects they created
themselves — enforced via _get_project_or_403() on every route that touches a specific project.
"""
from __future__ import annotations

import json
import logging
import os
import pathlib
import secrets

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

import auth
import db
import storage
from pipeline_runner import (
    DEMO_MODE,
    check_document_validity,
    extract_client_responses,
    finalize_proposal,
    process_manual_responses,
    process_new_upload,
    recompute_staffing_and_pricing,
)
# pipeline_runner's import above already inserts the pipeline/ package dir onto sys.path, so
# this resolves the same way it does inside pipeline_runner.py itself.
from pipeline.schema import Phase1Result

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

BASE_DIR = pathlib.Path(__file__).resolve().parent

app = FastAPI(title="RFP Proposal Agent")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# Order matters: the LAST middleware added is the OUTERMOST one, i.e. it runs first on the way
# in. SessionMiddleware must run before AuthGateMiddleware can read request.session, so it's
# added second (outer); AuthGateMiddleware is added first (inner).
_SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "false").lower() == "true"

app.add_middleware(auth.AuthGateMiddleware)
app.add_middleware(SessionMiddleware, secret_key=auth.get_session_secret(), same_site="lax",
                    https_only=_SESSION_COOKIE_SECURE)


@app.on_event("startup")
def startup():
    db.init_db()
    storage.ensure_bucket_exists()
    auth.ensure_bootstrap_admin()
    if not storage.is_encryption_enabled():
        print("[STARTUP WARNING] ENCRYPTION_KEY is not set — documents will be stored "
              "unencrypted at rest. See README for how to generate and set a production key.")
    if DEMO_MODE:
        print("[STARTUP WARNING] DEMO_MODE=true — uploads matching a bundled sample RFP will "
              "be processed with canned demo output instead of a real AI call. Do not use this "
              "setting against real client bid data.")
    db.log_action("app_started")


STATUS_LABELS = {
    "Analyzing": "Analyzing",
    "Not a Bid Document": "Not a Bid Document — Re-upload Needed",
    "Clarifications Sent": "Awaiting Client Clarification",
    "Responses Pending": "Responses Pending",
    "Awaiting Assumptions Approval": "Review Assumptions",
    "Awaiting Preview": "Review & Customize Proposal",
    "Ready to Generate": "Ready for Review",
    "Submitted": "Submitted",
    "Declined": "Declined",
    "Cancelled": "Cancelled",
}

# The order the pipeline actually moves projects through — drives the project-detail stepper.
# (The Home page's "how it works" panel is a separate, static explainer of the conceptual
# document-processing stages, not this per-project status sequence — see templates/home.html.)
# "Not a Bid Document" and "Cancelled" are terminal/branch statuses handled separately by the
# stepper (see terminal_branch_statuses in project_detail.html), not part of this main sequence.
STATUS_ORDER = [
    "Analyzing",
    "Clarifications Sent",
    "Responses Pending",
    "Awaiting Assumptions Approval",
    "Awaiting Preview",
    "Ready to Generate",
    "Submitted",
]

TERMINAL_BRANCH_STATUSES = {"Not a Bid Document", "Declined", "Cancelled"}


def _enrich_project(p: dict) -> dict:
    p = dict(p)
    p["status_label"] = STATUS_LABELS.get(p["status"], p["status"])
    if p.get("phase3_result_json"):
        p["total_price"] = json.loads(p["phase3_result_json"])["pricing"]["total"]
    else:
        p["total_price"] = None
    return p


def _ctx(request: Request, **extra) -> dict:
    """Common template context every page needs: who's logged in, the CSRF token for any
    forms on the page, and whether DEMO_MODE is active (shown as a persistent banner so it's
    never ambiguous whether output on screen is real)."""
    return {
        "current_user": auth.current_user(request),
        "csrf_token": request.session.get("csrf_token", ""),
        "demo_mode": DEMO_MODE,
        **extra,
    }


def _get_project_or_403(project_id: str, user: dict, allow_deleted: bool = False) -> dict:
    """Fetches a project and enforces per-user access: admins always pass; a member only passes
    if they created the project or an admin explicitly granted them access (db.project_access).
    Returns 404 rather than 403 for an unauthorized member, matching the existing soft-delete
    404 behavior — this deliberately avoids confirming to a member that a project ID exists at
    all when they have no access to it."""
    project = db.get_project(project_id)
    if not project or (project["deleted_at"] and not allow_deleted):
        raise HTTPException(404, "Project not found.")
    if not db.user_can_access_project(user, project):
        raise HTTPException(404, "Project not found.")
    return project


async def _read_capped(file: UploadFile, max_bytes: int) -> bytes:
    """Reads an upload in chunks, aborting as soon as it exceeds max_bytes, instead of buffering
    an arbitrarily large body into memory before checking its size (the original version's
    `await file.read()` had no cap at all)."""
    chunks = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise storage.UploadTooLarge(f"File is larger than the {max_bytes // (1024 * 1024)} MB limit.")
        chunks.append(chunk)
    return b"".join(chunks)


# ---------- Auth ----------

@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request, next: str = "/home"):
    if auth.current_user(request):
        return RedirectResponse("/home", status_code=303)
    request.session.setdefault("csrf_token", secrets.token_hex(32))
    return templates.TemplateResponse(request, "login.html", {
        "csrf_token": request.session["csrf_token"], "next": next, "error": None,
    })


@app.post("/login")
async def login_submit(request: Request):
    form = await request.form()
    username = str(form.get("username", ""))
    password = str(form.get("password", ""))
    next_path = str(form.get("next") or "/home")
    if not next_path.startswith("/"):
        next_path = "/home"  # never redirect off-site

    user = db.get_user_by_username(username)
    if not user or not user["is_active"] or not auth.verify_password(password, user["password_hash"]):
        db.log_action("login_failed", detail={"username": username}, user_identity=username or "unknown")
        request.session.setdefault("csrf_token", secrets.token_hex(32))
        return templates.TemplateResponse(request, "login.html", {
            "csrf_token": request.session["csrf_token"], "next": next_path,
            "error": "Incorrect username or password.",
        }, status_code=401)

    request.session.clear()
    request.session["user_id"] = user["id"]
    db.log_action("login_succeeded", user_identity=user["username"])
    return RedirectResponse(next_path, status_code=303)


@app.post("/logout")
def logout(request: Request, _: None = Depends(auth.verify_csrf)):
    user = auth.current_user(request)
    if user:
        db.log_action("logout", user_identity=user["username"])
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


# ---------- Home ----------

@app.get("/home", response_class=HTMLResponse)
def home(request: Request):
    user = auth.current_user(request)
    projects = [_enrich_project(p) for p in db.list_projects_for_user(user)]

    awaiting_client = sum(1 for p in projects if p["status"] in ("Clarifications Sent", "Responses Pending"))
    in_review = sum(1 for p in projects if p["status"] in
                    ("Analyzing", "Awaiting Assumptions Approval", "Awaiting Preview", "Ready to Generate"))
    submitted = sum(1 for p in projects if p["status"] == "Submitted")
    needs_attention = sum(1 for p in projects if p.get("error_message"))

    recent_projects = projects[:5]
    recent_activity = db.list_audit_log_for_user(user, limit=8)

    return templates.TemplateResponse(request, "home.html", _ctx(request,
        total_projects=len(projects),
        awaiting_client=awaiting_client,
        in_review=in_review,
        submitted=submitted,
        needs_attention=needs_attention,
        recent_projects=recent_projects,
        recent_activity=recent_activity,
    ))


# ---------- Dashboard / projects ----------

@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    user = auth.current_user(request)
    projects = [_enrich_project(p) for p in db.list_projects_for_user(user)]
    return templates.TemplateResponse(request, "dashboard.html", _ctx(request, projects=projects))


@app.get("/new", response_class=HTMLResponse)
def new_project_form(request: Request):
    return templates.TemplateResponse(request, "new_project.html", _ctx(request))


@app.post("/projects")
async def create_project(
    request: Request,
    background_tasks: BackgroundTasks,
    project_name: str = Form(...),
    bid_file: UploadFile = File(...),
    _: None = Depends(auth.verify_csrf),
):
    user = auth.current_user(request)
    if not project_name.strip():
        raise HTTPException(400, "Project name is required.")

    try:
        content = await _read_capped(bid_file, storage.MAX_UPLOAD_BYTES)
        if not content:
            raise HTTPException(400, "Uploaded file is empty.")
        extension = storage.validate_extension(bid_file.filename, storage.ALLOWED_UPLOAD_EXTENSIONS)
    except storage.UnsupportedFileType as e:
        raise HTTPException(400, str(e)) from e
    except storage.UploadTooLarge as e:
        raise HTTPException(413, str(e)) from e

    # Bid-document validity check now runs synchronously, right here, before any project row is
    # created. This replaces the old flow where the check only happened inside the background
    # pipeline task after the user had already been redirected away to a newly-created project
    # page — so a rejected upload left a permanent "Not a Bid Document" row cluttering the
    # dashboard, and the uploader only found out about the rejection by revisiting or refreshing
    # that page. Now the uploader gets an immediate answer on this same page, and nothing is
    # created or stored at all for a rejected file.
    validity_already_checked = False
    try:
        validity = check_document_validity(content, extension)
    except Exception:
        # A transient LLM/classification failure here shouldn't block the upload outright —
        # fall through and let the background pipeline's own validity check (and its existing
        # error handling) take another pass at it, same as before this synchronous check existed.
        logging.getLogger("rfp_agent").exception(
            "Synchronous bid-document validity check failed; deferring to the background pipeline"
        )
        validity = None

    if validity is not None:
        validity_already_checked = True
        db.log_action(
            "document_validity_checked", None,
            {**validity.to_dict(), "project_name": project_name.strip()},
            user_identity=user["username"],
        )
        if not validity.is_bid_document:
            error = "This file doesn't look like a bid invitation or RFP — no project was created."
            if validity.reasoning:
                error += f" {validity.reasoning}"
            return templates.TemplateResponse(
                request, "new_project.html",
                _ctx(request, error=error, project_name=project_name),
                status_code=400,
            )

    project_id = db.create_project(name=project_name.strip(), created_by=user["username"])
    display_name, stored_path, encrypted = storage.save_upload(project_id, bid_file.filename, content)
    db.add_document(project_id, "bid_invitation", display_name, stored_path, encrypted)
    db.log_action("project_created", project_id, {"name": project_name, "filename": display_name},
                  user_identity=user["username"])

    background_tasks.add_task(
        process_new_upload, project_id, content, extension, skip_validity_check=validity_already_checked
    )

    return RedirectResponse(f"/projects/{project_id}", status_code=303)


@app.get("/projects/{project_id}", response_class=HTMLResponse)
def project_detail(request: Request, project_id: str):
    user = auth.current_user(request)
    project = _get_project_or_403(project_id, user)
    project = _enrich_project(project)
    documents = db.list_documents(project_id)

    requirements = []
    if project.get("phase1_result_json"):
        requirements = json.loads(project["phase1_result_json"])["requirements"]

    assumption_items = [r for r in requirements if r["status"] == "assumption_needed"]

    # Every currently-open clarification question gets its own text box on the page: one group
    # that's still actively blocking the pipeline (needs an answer before it can continue) and
    # one group that's already been escalated for manual review (a previous answer was judged
    # insufficient — it no longer blocks automatic progress, but the user can still revise it).
    # See Phase1Result.get_blocking() in pipeline/schema.py for the exact same split used
    # server-side to decide whether the pipeline can actually proceed.
    open_clarification_items = [r for r in requirements if r["status"] == "ambiguous"]
    blocking_clarification_items = [r for r in open_clarification_items if not r.get("escalated_for_manual_review")]
    escalated_clarification_items = [r for r in open_clarification_items if r.get("escalated_for_manual_review")]

    pending_responses = {}
    if project.get("pending_client_responses_json"):
        pending_responses = json.loads(project["pending_client_responses_json"])

    capability_fit = None
    if project.get("capability_fit_json"):
        capability_fit = json.loads(project["capability_fit_json"])

    compliance_matrix = []
    pricing = None
    preview_content = None
    if project.get("phase4_content_json"):
        content = json.loads(project["phase4_content_json"])
        compliance_matrix = content.get("compliance_matrix", [])
        pricing = content.get("pricing")
        if project["status"] == "Awaiting Preview":
            preview_content = content

    return templates.TemplateResponse(request, "project_detail.html", _ctx(request,
        project=project,
        documents=documents,
        requirements=requirements,
        assumption_items=assumption_items,
        open_clarification_items=open_clarification_items,
        blocking_clarification_items=blocking_clarification_items,
        escalated_clarification_items=escalated_clarification_items,
        pending_responses=pending_responses,
        no_answers_found=request.query_params.get("no_answers_found") == "1",
        capability_fit=capability_fit,
        compliance_matrix=compliance_matrix,
        pricing=pricing,
        preview_content=preview_content,
        can_upload_responses=project["status"] in ("Clarifications Sent", "Responses Pending"),
    ))


@app.post("/projects/{project_id}/responses")
async def upload_responses(
    request: Request,
    project_id: str,
    responses_file: UploadFile = File(...),
    _: None = Depends(auth.verify_csrf),
):
    """Auto-fill step only: parses an uploaded filled-in clarification doc and stages whatever
    answers it found into pending_client_responses_json, so they show up pre-filled in the
    per-question text boxes on the project page below. Nothing is applied to the pipeline here —
    the user reviews (and can edit) the pre-filled text, then uses "Submit Responses & Resume"
    (submit_client_responses below) to actually continue. This replaced the old behavior of
    applying the doc immediately on upload, which gave the user no chance to see or correct what
    was about to be submitted, and no clear explanation when a submission didn't move things
    forward."""
    user = auth.current_user(request)
    project = _get_project_or_403(project_id, user)
    if project["status"] not in ("Clarifications Sent", "Responses Pending"):
        raise HTTPException(400, "This project isn't waiting on clarification responses.")

    try:
        content = await _read_capped(responses_file, storage.MAX_UPLOAD_BYTES)
        storage.validate_extension(responses_file.filename, {".docx"})
    except storage.UnsupportedFileType as e:
        raise HTTPException(400, str(e)) from e
    except storage.UploadTooLarge as e:
        raise HTTPException(413, str(e)) from e

    display_name, stored_path, encrypted = storage.save_upload(
        project_id, f"responses_{responses_file.filename}", content
    )
    db.add_document(project_id, "client_responses", display_name, stored_path, encrypted)

    try:
        extracted = extract_client_responses(project_id, content)
    except Exception:
        logging.getLogger("rfp_agent").exception(
            "Failed to auto-fill clarification responses from an uploaded document"
        )
        extracted = {}

    db.update_project(project_id, pending_client_responses_json=json.dumps(extracted))
    db.log_action(
        "responses_autofetched", project_id,
        {"filename": display_name, "matched_count": len(extracted)},
        user_identity=user["username"],
    )

    suffix = "" if extracted else "?no_answers_found=1"
    return RedirectResponse(f"/projects/{project_id}{suffix}", status_code=303)


@app.post("/projects/{project_id}/responses/submit")
async def submit_client_responses(
    request: Request,
    background_tasks: BackgroundTasks,
    project_id: str,
    _: None = Depends(auth.verify_csrf),
):
    """The actual clarification-response submission — reads whatever is currently in each
    question's text box (typed by hand, auto-filled from an uploaded doc via upload_responses
    above, or a previous answer being revised) and applies it. Every currently-open question
    (ambiguous, whether already escalated or not) gets its own text box on the project page, so
    this reads response_<requirement_id> fields rather than re-parsing any document."""
    user = auth.current_user(request)
    project = _get_project_or_403(project_id, user)
    if project["status"] not in ("Clarifications Sent", "Responses Pending"):
        raise HTTPException(400, "This project isn't waiting on clarification responses.")
    if not project.get("phase1_result_json"):
        raise HTTPException(400, "No extracted requirements found for this project.")

    form = await request.form()
    result = Phase1Result.from_dict(json.loads(project["phase1_result_json"]))
    open_items = [r for r in result.requirements if r.status == "ambiguous"]

    responses: dict[str, str] = {}
    for item in open_items:
        val = form.get(f"response_{item.id}")
        if val is not None and str(val).strip():
            responses[item.id] = str(val).strip()

    if not responses:
        raise HTTPException(400, "Enter at least one response before submitting.")

    db.log_action(
        "responses_submitted", project_id, {"answered_count": len(responses)},
        user_identity=user["username"],
    )
    db.update_project(project_id, status="Responses Pending", pending_client_responses_json=None)
    background_tasks.add_task(process_manual_responses, project_id, responses)

    return RedirectResponse(f"/projects/{project_id}", status_code=303)


@app.get("/projects/{project_id}/documents/{doc_id}")
def download_document(request: Request, project_id: str, doc_id: str):
    user = auth.current_user(request)
    doc = db.get_document(doc_id)
    if not doc or doc["project_id"] != project_id:
        raise HTTPException(404, "Document not found.")
    _get_project_or_403(project_id, user)

    # Decrypted straight into memory and streamed back — no plaintext temp file is written to
    # disk at all (the old version wrote one to GENERATED_DIR and never deleted it).
    content = storage.read_file_bytes(doc["storage_path"], bool(doc["encrypted"]))
    db.log_action("document_downloaded", project_id, {"doc_id": doc_id, "filename": doc["filename"]},
                  user_identity=user["username"])

    safe_name = storage.sanitize_display_name(doc["filename"])
    return Response(
        content=content,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}"'},
    )


def _require_ready_to_generate(project_id: str, user: dict) -> dict:
    """'Ready to Generate' now means: the user finalized the editable preview and a final .docx
    has actually been built (see pipeline_runner.py::finalize_proposal). Everything before that
    — Analyzing, the assumptions gate, the preview itself — flips automatically or by the user's
    own preview/assumptions actions; approve/reject is the one remaining manual decision, so both
    routes below re-check the current status (and the caller's access) server-side rather than
    trusting whatever the form said."""
    project = _get_project_or_403(project_id, user)
    if project["status"] != "Ready to Generate":
        raise HTTPException(400, "This project isn't at the review step yet — there's nothing to approve or reject.")
    return project


@app.post("/projects/{project_id}/approve")
def approve_project(request: Request, project_id: str, _: None = Depends(auth.verify_csrf)):
    user = auth.current_user(request)
    _require_ready_to_generate(project_id, user)
    db.update_project(project_id, status="Submitted")
    db.log_action("project_approved", project_id, user_identity=user["username"])
    return RedirectResponse(f"/projects/{project_id}", status_code=303)


@app.post("/projects/{project_id}/reject")
def reject_project(request: Request, project_id: str, _: None = Depends(auth.verify_csrf)):
    user = auth.current_user(request)
    _require_ready_to_generate(project_id, user)
    db.update_project(project_id, status="Declined")
    db.log_action("project_declined", project_id, user_identity=user["username"])
    return RedirectResponse(f"/projects/{project_id}", status_code=303)


@app.post("/projects/{project_id}/reupload")
async def reupload_bid_document(
    request: Request,
    background_tasks: BackgroundTasks,
    project_id: str,
    bid_file: UploadFile = File(...),
    _: None = Depends(auth.verify_csrf),
):
    """Lets the user re-upload a different file to the same project after a 'Not a Bid Document'
    halt, and re-runs process_new_upload from scratch on it — the same entry point a brand new
    project uses, so the new file gets its own validity check, its own Phase 1, etc."""
    user = auth.current_user(request)
    project = _get_project_or_403(project_id, user)
    if project["status"] != "Not a Bid Document":
        raise HTTPException(400, "This project isn't waiting on a re-upload.")

    try:
        content = await _read_capped(bid_file, storage.MAX_UPLOAD_BYTES)
        if not content:
            raise HTTPException(400, "Uploaded file is empty.")
        extension = storage.validate_extension(bid_file.filename, storage.ALLOWED_UPLOAD_EXTENSIONS)
    except storage.UnsupportedFileType as e:
        raise HTTPException(400, str(e)) from e
    except storage.UploadTooLarge as e:
        raise HTTPException(413, str(e)) from e

    display_name, stored_path, encrypted = storage.save_upload(project_id, bid_file.filename, content)
    db.add_document(project_id, "bid_invitation", display_name, stored_path, encrypted)
    db.log_action("project_reuploaded", project_id, {"filename": display_name}, user_identity=user["username"])

    # Defensively reset — Phase 1 never actually ran on the rejected attempt, but this keeps the
    # project row clean of anything stale from before the re-upload.
    db.update_project(
        project_id,
        status="Analyzing",
        error_message=None,
        validity_rejection_reason=None,
        phase1_result_json=None,
        capability_fit_json=None,
    )
    background_tasks.add_task(process_new_upload, project_id, content, extension)

    return RedirectResponse(f"/projects/{project_id}", status_code=303)


@app.post("/projects/{project_id}/assumptions/accept")
def accept_assumptions(request: Request, project_id: str, _: None = Depends(auth.verify_csrf)):
    user = auth.current_user(request)
    project = _get_project_or_403(project_id, user)
    if project["status"] != "Awaiting Assumptions Approval":
        raise HTTPException(400, "This project isn't waiting on an assumptions decision.")
    db.update_project(project_id, status="Awaiting Preview")
    db.log_action("assumptions_accepted", project_id, user_identity=user["username"])
    return RedirectResponse(f"/projects/{project_id}", status_code=303)


@app.post("/projects/{project_id}/assumptions/cancel")
def cancel_assumptions(request: Request, project_id: str, _: None = Depends(auth.verify_csrf)):
    user = auth.current_user(request)
    project = _get_project_or_403(project_id, user)
    if project["status"] != "Awaiting Assumptions Approval":
        raise HTTPException(400, "This project isn't waiting on an assumptions decision.")
    db.update_project(project_id, status="Cancelled")
    db.log_action("assumptions_cancelled", project_id, user_identity=user["username"])
    return RedirectResponse(f"/projects/{project_id}", status_code=303)


_EDITABLE_NARRATIVE_FIELDS = ["executive_summary", "understanding", "technology_approach",
                              "past_performance", "closing"]


@app.post("/projects/{project_id}/preview/generate")
async def generate_final_proposal(
    request: Request,
    background_tasks: BackgroundTasks,
    project_id: str,
    _: None = Depends(auth.verify_csrf),
):
    """The editable-preview submit route. Reassembles the persisted phase4_content_json with
    whatever the user edited — narrative text, the assumptions list, staffing/timeline/compliance
    tables — recomputes pricing from the (possibly edited) staffing numbers, optionally stores an
    uploaded custom template, and hands everything to finalize_proposal() as a background task
    (it does real work: building the docx and uploading it)."""
    user = auth.current_user(request)
    project = _get_project_or_403(project_id, user)
    if project["status"] != "Awaiting Preview":
        raise HTTPException(400, "This project isn't at the preview/customize step.")
    if not project.get("phase4_content_json"):
        raise HTTPException(400, "No generated content found to preview.")

    form = await request.form()
    content = json.loads(project["phase4_content_json"])

    for field in _EDITABLE_NARRATIVE_FIELDS:
        if field in form:
            content[field] = str(form[field])

    if "assumptions" in form:
        content["assumptions"] = [line.strip() for line in str(form["assumptions"]).split("\n") if line.strip()]

    # Staffing/pricing: role and hourly_rate are read-only (tied to the rate card); only
    # headcount and hours_per_person are user-editable. Recomputing via the same deterministic
    # pricing engine used by Phase 3 keeps total_hours/subtotal/labor_subtotal/contingency/total
    # arithmetically consistent no matter what the user changed.
    staffing_row_count = int(form.get("staffing_row_count", 0) or 0)
    if staffing_row_count:
        edited_staffing_plan = []
        for i in range(staffing_row_count):
            role = form.get(f"staffing_role_{i}")
            if not role:
                continue
            edited_staffing_plan.append({
                "role": str(role),
                "headcount": int(form.get(f"staffing_headcount_{i}", 0) or 0),
                "hours_per_person": int(form.get(f"staffing_hours_per_person_{i}", 0) or 0),
            })
        if edited_staffing_plan:
            pricing = recompute_staffing_and_pricing(edited_staffing_plan)
            content["pricing"] = pricing
            content["staffing"] = pricing["lines"]

    # Timeline: phase/duration/description are all free text, no arithmetic involved.
    timeline_row_count = int(form.get("timeline_row_count", 0) or 0)
    if timeline_row_count:
        edited_timeline = []
        for i in range(timeline_row_count):
            phase = form.get(f"timeline_phase_{i}")
            if not phase:
                continue
            edited_timeline.append({
                "phase": str(phase),
                "duration": str(form.get(f"timeline_duration_{i}", "")),
                "description": str(form.get(f"timeline_description_{i}", "")),
            })
        if edited_timeline:
            content["timeline"] = edited_timeline

    # Compliance matrix: requirement_id/requirement stay tied to the actual extracted
    # requirements (read-only); only the response text and status are editable.
    compliance_row_count = int(form.get("compliance_row_count", 0) or 0)
    if compliance_row_count:
        existing_matrix = content.get("compliance_matrix", [])
        edited_matrix = []
        for i in range(compliance_row_count):
            if i >= len(existing_matrix):
                break
            entry = dict(existing_matrix[i])
            if f"compliance_response_{i}" in form:
                entry["response"] = str(form[f"compliance_response_{i}"])
            if f"compliance_status_{i}" in form:
                entry["status"] = str(form[f"compliance_status_{i}"])
            edited_matrix.append(entry)
        if edited_matrix:
            content["compliance_matrix"] = edited_matrix

    template_choice = str(form.get("template_choice", "default"))
    if template_choice not in ("default", "custom"):
        template_choice = "default"

    custom_template_bytes = None
    if template_choice == "custom":
        custom_template = form.get("custom_template")
        if not isinstance(custom_template, UploadFile) or not custom_template.filename:
            raise HTTPException(400, "Please upload a .docx template, or choose the default template.")
        try:
            storage.validate_extension(custom_template.filename, {".docx"})
        except storage.UnsupportedFileType as e:
            raise HTTPException(400, str(e)) from e
        custom_template_bytes = await _read_capped(custom_template, storage.MAX_UPLOAD_BYTES)
        display_name, stored_path, encrypted = storage.save_upload(
            project_id, custom_template.filename, custom_template_bytes
        )
        db.add_document(project_id, "custom_template", display_name, stored_path, encrypted)

    db.update_project(project_id, template_choice=template_choice)
    db.log_action("preview_submitted", project_id, {"template_choice": template_choice},
                  user_identity=user["username"])
    background_tasks.add_task(finalize_proposal, project_id, content, template_choice, custom_template_bytes)

    return RedirectResponse(f"/projects/{project_id}", status_code=303)


@app.post("/projects/{project_id}/delete")
def delete_project(request: Request, project_id: str, _: None = Depends(auth.verify_csrf)):
    user = auth.current_user(request)
    _get_project_or_403(project_id, user)
    db.soft_delete_project(project_id)
    db.log_action("project_deleted", project_id, user_identity=user["username"])
    return RedirectResponse("/", status_code=303)


@app.get("/audit", response_class=HTMLResponse)
def audit_log(request: Request):
    user = auth.current_user(request)
    entries = db.list_audit_log_for_user(user)
    return templates.TemplateResponse(request, "audit.html", _ctx(request, entries=entries))


# ---------- Admin: user management ----------

def _admin_users_ctx(request: Request, error: str | None = None) -> dict:
    users = db.list_users()
    return _ctx(
        request,
        users=users,
        projects=db.list_projects(),
        user_access={u["id"]: db.list_accessible_project_ids(u["id"]) for u in users},
        error=error,
    )


@app.get("/admin/users", response_class=HTMLResponse)
def admin_users(request: Request):
    return templates.TemplateResponse(request, "admin_users.html", _admin_users_ctx(request))


@app.post("/admin/users")
def admin_create_user(request: Request, username: str = Form(...), password: str = Form(...),
                       role: str = Form("member"), project_ids: list[str] = Form([]),
                       _: None = Depends(auth.verify_csrf)):
    admin_user = auth.current_user(request)
    username = username.strip().lower()
    role = role if role in ("admin", "member") else "member"

    error = None
    if not username or not password:
        error = "Username and password are required."
    elif len(password) < 8:
        error = "Password must be at least 8 characters."
    elif db.get_user_by_username(username):
        error = f"A user named '{username}' already exists."

    if error:
        return templates.TemplateResponse(request, "admin_users.html",
                                           _admin_users_ctx(request, error=error), status_code=400)

    new_user_id = db.create_user(username, auth.hash_password(password), role=role, created_by=admin_user["username"])
    if project_ids:
        db.set_user_project_access(new_user_id, project_ids, granted_by=admin_user["username"])
    db.log_action("user_created", detail={"new_username": username, "role": role, "project_ids": project_ids},
                  user_identity=admin_user["username"])
    return RedirectResponse("/admin/users", status_code=303)


@app.post("/admin/users/{user_id}/access")
def admin_update_user_access(request: Request, user_id: str, project_ids: list[str] = Form([]),
                              _: None = Depends(auth.verify_csrf)):
    admin_user = auth.current_user(request)
    target = db.get_user(user_id)
    if not target:
        raise HTTPException(404, "User not found.")
    db.set_user_project_access(user_id, project_ids, granted_by=admin_user["username"])
    db.log_action("user_access_updated", detail={"target_username": target["username"], "project_ids": project_ids},
                  user_identity=admin_user["username"])
    return RedirectResponse("/admin/users", status_code=303)


@app.post("/admin/users/{user_id}/deactivate")
def admin_deactivate_user(request: Request, user_id: str, _: None = Depends(auth.verify_csrf)):
    admin_user = auth.current_user(request)
    target = db.get_user(user_id)
    if not target:
        raise HTTPException(404, "User not found.")
    if target["id"] == admin_user["id"]:
        raise HTTPException(400, "You can't deactivate your own account.")
    if target["role"] == "admin" and target["is_active"] and db.count_active_admins() <= 1:
        raise HTTPException(400, "You can't deactivate the last active admin — promote another "
                                  "account to admin first.")
    db.set_user_active(user_id, False)
    db.log_action("user_deactivated", detail={"target_username": target["username"]}, user_identity=admin_user["username"])
    return RedirectResponse("/admin/users", status_code=303)


@app.post("/admin/users/{user_id}/delete")
def admin_delete_user(request: Request, user_id: str, _: None = Depends(auth.verify_csrf)):
    admin_user = auth.current_user(request)
    target = db.get_user(user_id)
    if not target:
        raise HTTPException(404, "User not found.")
    if target["id"] == admin_user["id"]:
        raise HTTPException(400, "You can't delete your own account.")
    if target["role"] == "admin" and target["is_active"] and db.count_active_admins() <= 1:
        raise HTTPException(400, "You can't delete the last active admin — promote another "
                                  "account to admin first.")
    db.delete_user(user_id)
    db.log_action("user_deleted", detail={"target_username": target["username"]}, user_identity=admin_user["username"])
    return RedirectResponse("/admin/users", status_code=303)


@app.post("/admin/users/{user_id}/activate")
def admin_activate_user(request: Request, user_id: str, _: None = Depends(auth.verify_csrf)):
    admin_user = auth.current_user(request)
    target = db.get_user(user_id)
    if not target:
        raise HTTPException(404, "User not found.")
    db.set_user_active(user_id, True)
    db.log_action("user_activated", detail={"target_username": target["username"]}, user_identity=admin_user["username"])
    return RedirectResponse("/admin/users", status_code=303)
