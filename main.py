"""
RFP Proposal Agent — web application.

Routes:
  GET  /                              dashboard (project list)
  GET  /new                           upload form
  POST /projects                      create project + upload bid invitation, kicks off pipeline
  GET  /projects/{id}                 project detail view
  POST /projects/{id}/responses       upload filled-in client responses, resumes pipeline
  GET  /projects/{id}/documents/{did} download a document
  POST /projects/{id}/status          manually update status (e.g. mark Submitted)
  POST /projects/{id}/delete          soft-delete a project
  GET  /audit                         audit log view
"""
from __future__ import annotations

import json
import pathlib

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import db
import storage
from pipeline_runner import process_client_responses, process_new_upload

BASE_DIR = pathlib.Path(__file__).resolve().parent

app = FastAPI(title="RFP Proposal Agent")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@app.on_event("startup")
def startup():
    db.init_db()
    if not storage.is_encryption_enabled():
        print("[STARTUP WARNING] ENCRYPTION_KEY is not set — documents will be stored "
              "unencrypted at rest. See README for how to generate and set a production key.")
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


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    projects = [_enrich_project(p) for p in db.list_projects()]
    return templates.TemplateResponse(request, "dashboard.html", {"projects": projects})


@app.get("/new", response_class=HTMLResponse)
def new_project_form(request: Request):
    return templates.TemplateResponse(request, "new_project.html", {})


@app.post("/projects")
async def create_project(
    background_tasks: BackgroundTasks,
    project_name: str = Form(...),
    bid_file: UploadFile = File(...),
):
    if not project_name.strip():
        raise HTTPException(400, "Project name is required.")
    content = await bid_file.read()
    if not content:
        raise HTTPException(400, "Uploaded file is empty.")

    project_id = db.create_project(name=project_name.strip())
    stored_path = storage.save_upload(project_id, bid_file.filename, content)
    db.add_document(project_id, "bid_invitation", bid_file.filename, stored_path)
    db.log_action("project_created", project_id, {"name": project_name, "filename": bid_file.filename})

    # Write a plain-text working copy for the pipeline to read (handles encryption transparently)
    working_dir = storage.UPLOADS_DIR / project_id / "_working"
    working_dir.mkdir(parents=True, exist_ok=True)
    working_path = working_dir / bid_file.filename
    working_path.write_bytes(content)

    background_tasks.add_task(process_new_upload, project_id, str(working_path))

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

    compliance_matrix = []
    pricing = None
    if project.get("phase4_content_json"):
        content = json.loads(project["phase4_content_json"])
        compliance_matrix = content.get("compliance_matrix", [])
        pricing = content.get("pricing")

    return templates.TemplateResponse(request, "project_detail.html", {
        "project": project,
        "documents": documents,
        "requirements": requirements,
        "compliance_matrix": compliance_matrix,
        "pricing": pricing,
        "can_upload_responses": project["status"] in ("Clarifications Sent", "Responses Pending"),
    })


@app.post("/projects/{project_id}/responses")
async def upload_responses(
    background_tasks: BackgroundTasks,
    project_id: str,
    responses_file: UploadFile = File(...),
):
    project = db.get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found.")

    content = await responses_file.read()
    stored_path = storage.save_upload(project_id, f"responses_{responses_file.filename}", content)
    db.add_document(project_id, "client_responses", responses_file.filename, stored_path)
    db.log_action("responses_uploaded", project_id, {"filename": responses_file.filename})

    working_dir = storage.UPLOADS_DIR / project_id / "_working"
    working_dir.mkdir(parents=True, exist_ok=True)
    working_path = working_dir / f"responses_{responses_file.filename}"
    working_path.write_bytes(content)

    db.update_project(project_id, status="Responses Pending")
    background_tasks.add_task(process_client_responses, project_id, str(working_path))

    return RedirectResponse(f"/projects/{project_id}", status_code=303)


@app.get("/projects/{project_id}/documents/{doc_id}")
def download_document(project_id: str, doc_id: str):
    doc = db.get_document(doc_id)
    if not doc or doc["project_id"] != project_id:
        raise HTTPException(404, "Document not found.")
    content = storage.read_file(doc["storage_path"])
    db.log_action("document_downloaded", project_id, {"doc_id": doc_id, "filename": doc["filename"]})

    tmp_path = storage.GENERATED_DIR / f"_dl_{doc_id}_{doc['filename']}"
    tmp_path.write_bytes(content)
    return FileResponse(str(tmp_path), filename=doc["filename"])


@app.post("/projects/{project_id}/status")
def update_status(project_id: str, status: str = Form(...)):
    if status not in db.VALID_STATUSES:
        raise HTTPException(400, f"Invalid status. Must be one of: {db.VALID_STATUSES}")
    db.update_project(project_id, status=status)
    db.log_action("status_changed_manually", project_id, {"new_status": status})
    return RedirectResponse(f"/projects/{project_id}", status_code=303)


@app.post("/projects/{project_id}/delete")
def delete_project(project_id: str):
    db.soft_delete_project(project_id)
    db.log_action("project_deleted", project_id)
    return RedirectResponse("/", status_code=303)


@app.get("/audit", response_class=HTMLResponse)
def audit_log(request: Request):
    entries = db.list_audit_log()
    return templates.TemplateResponse(request, "audit.html", {"entries": entries})
