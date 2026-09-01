"""
RFP Proposal Agent — web application.

Routes:
  GET  /                              dashboard (project list)
  GET  /login, POST /login            sign in
  POST /logout                        sign out
  GET  /new                           upload form
  POST /projects                      create project + upload bid invitation, kicks off pipeline
  GET  /projects/{id}                 project detail view
  POST /projects/{id}/responses       upload filled-in client responses, resumes pipeline
  GET  /projects/{id}/documents/{did} download a document
  POST /projects/{id}/status          manually update status (e.g. mark Submitted)
  POST /projects/{id}/delete          soft-delete a project
  GET  /audit                         audit log view
  GET  /admin/users, POST /admin/users, POST /admin/users/{id}/deactivate   admin-only user management

Every route above except /login and /static/* requires a logged-in, active user — enforced by
auth.AuthGateMiddleware, not by a per-route check, so a route added later can't accidentally
ship without one. Every POST route also requires a valid CSRF token (auth.verify_csrf).
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
from pipeline_runner import DEMO_MODE, process_client_responses, process_new_upload

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
    "Clarifications Sent": "Awaiting Client Clarification",
    "Responses Pending": "Responses Pending",
    "Ready to Generate": "Ready for Review",
    "Submitted": "Submitted",
}


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
def login_form(request: Request, next: str = "/"):
    if auth.current_user(request):
        return RedirectResponse("/", status_code=303)
    request.session.setdefault("csrf_token", secrets.token_hex(32))
    return templates.TemplateResponse(request, "login.html", {
        "csrf_token": request.session["csrf_token"], "next": next, "error": None,
    })


@app.post("/login")
async def login_submit(request: Request):
    form = await request.form()
    username = str(form.get("username", ""))
    password = str(form.get("password", ""))
    next_path = str(form.get("next") or "/")
    if not next_path.startswith("/"):
        next_path = "/"  # never redirect off-site

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


# ---------- Dashboard / projects ----------

@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    projects = [_enrich_project(p) for p in db.list_projects()]
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

    project_id = db.create_project(name=project_name.strip(), created_by=user["username"])
    display_name, stored_path, encrypted = storage.save_upload(project_id, bid_file.filename, content)
    db.add_document(project_id, "bid_invitation", display_name, stored_path, encrypted)
    db.log_action("project_created", project_id, {"name": project_name, "filename": display_name},
                  user_identity=user["username"])

    background_tasks.add_task(process_new_upload, project_id, content, extension)

    return RedirectResponse(f"/projects/{project_id}", status_code=303)


@app.get("/projects/{project_id}", response_class=HTMLResponse)
def project_detail(request: Request, project_id: str):
    project = db.get_project(project_id)
    if not project or project["deleted_at"]:
        raise HTTPException(404, "Project not found.")
    project = _enrich_project(project)
    documents = db.list_documents(project_id)

    requirements = []
    if project.get("phase1_result_json"):
        requirements = json.loads(project["phase1_result_json"])["requirements"]

    capability_fit = None
    if project.get("capability_fit_json"):
        capability_fit = json.loads(project["capability_fit_json"])

    compliance_matrix = []
    pricing = None
    if project.get("phase4_content_json"):
        content = json.loads(project["phase4_content_json"])
        compliance_matrix = content.get("compliance_matrix", [])
        pricing = content.get("pricing")

    return templates.TemplateResponse(request, "project_detail.html", _ctx(request,
        project=project,
        documents=documents,
        requirements=requirements,
        capability_fit=capability_fit,
        compliance_matrix=compliance_matrix,
        pricing=pricing,
        can_upload_responses=project["status"] in ("Clarifications Sent", "Responses Pending"),
    ))


@app.post("/projects/{project_id}/responses")
async def upload_responses(
    request: Request,
    background_tasks: BackgroundTasks,
    project_id: str,
    responses_file: UploadFile = File(...),
    _: None = Depends(auth.verify_csrf),
):
    user = auth.current_user(request)
    project = db.get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found.")

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
    db.log_action("responses_uploaded", project_id, {"filename": display_name}, user_identity=user["username"])

    db.update_project(project_id, status="Responses Pending")
    background_tasks.add_task(process_client_responses, project_id, content)

    return RedirectResponse(f"/projects/{project_id}", status_code=303)


@app.get("/projects/{project_id}/documents/{doc_id}")
def download_document(request: Request, project_id: str, doc_id: str):
    user = auth.current_user(request)
    doc = db.get_document(doc_id)
    if not doc or doc["project_id"] != project_id:
        raise HTTPException(404, "Document not found.")

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


@app.post("/projects/{project_id}/status")
def update_status(request: Request, project_id: str, status: str = Form(...), _: None = Depends(auth.verify_csrf)):
    user = auth.current_user(request)
    if status not in db.VALID_STATUSES:
        raise HTTPException(400, f"Invalid status. Must be one of: {db.VALID_STATUSES}")
    db.update_project(project_id, status=status)
    db.log_action("status_changed_manually", project_id, {"new_status": status}, user_identity=user["username"])
    return RedirectResponse(f"/projects/{project_id}", status_code=303)


@app.post("/projects/{project_id}/delete")
def delete_project(request: Request, project_id: str, _: None = Depends(auth.verify_csrf)):
    user = auth.current_user(request)
    db.soft_delete_project(project_id)
    db.log_action("project_deleted", project_id, user_identity=user["username"])
    return RedirectResponse("/", status_code=303)


@app.get("/audit", response_class=HTMLResponse)
def audit_log(request: Request):
    entries = db.list_audit_log()
    return templates.TemplateResponse(request, "audit.html", _ctx(request, entries=entries))


# ---------- Admin: user management ----------

@app.get("/admin/users", response_class=HTMLResponse)
def admin_users(request: Request):
    return templates.TemplateResponse(request, "admin_users.html", _ctx(request, users=db.list_users(), error=None))


@app.post("/admin/users")
def admin_create_user(request: Request, username: str = Form(...), password: str = Form(...),
                       role: str = Form("member"), _: None = Depends(auth.verify_csrf)):
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
                                           _ctx(request, users=db.list_users(), error=error), status_code=400)

    db.create_user(username, auth.hash_password(password), role=role, created_by=admin_user["username"])
    db.log_action("user_created", detail={"new_username": username, "role": role}, user_identity=admin_user["username"])
    return RedirectResponse("/admin/users", status_code=303)


@app.post("/admin/users/{user_id}/deactivate")
def admin_deactivate_user(request: Request, user_id: str, _: None = Depends(auth.verify_csrf)):
    admin_user = auth.current_user(request)
    target = db.get_user(user_id)
    if not target:
        raise HTTPException(404, "User not found.")
    if target["id"] == admin_user["id"]:
        raise HTTPException(400, "You can't deactivate your own account.")
    db.set_user_active(user_id, False)
    db.log_action("user_deactivated", detail={"target_username": target["username"]}, user_identity=admin_user["username"])
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
