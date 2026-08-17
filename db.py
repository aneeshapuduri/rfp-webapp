"""
SQLite data layer. Deliberately plain sqlite3 (no ORM) to keep the deployment footprint
small — one file, no extra service to run. Swap for Postgres later without touching callers
much, since all access goes through these functions.
"""
from __future__ import annotations

import datetime
import json
import pathlib
import sqlite3
import uuid

DB_PATH = pathlib.Path(__file__).resolve().parent / "data" / "app.db"

VALID_STATUSES = [
    "Analyzing",
    "Clarifications Sent",
    "Responses Pending",
    "Ready to Generate",
    "Submitted",
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    agency TEXT,
    status TEXT NOT NULL DEFAULT 'Analyzing',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deleted_at TEXT,
    error_message TEXT,
    phase1_result_json TEXT,
    phase3_result_json TEXT,
    phase4_content_json TEXT,
    clarification_mapping_json TEXT,
    duration_months REAL DEFAULT 9
);

CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    doc_type TEXT NOT NULL,   -- 'bid_invitation' | 'clarification_questions' | 'client_responses' | 'final_proposal'
    filename TEXT NOT NULL,
    storage_path TEXT NOT NULL,
    uploaded_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id)
);

CREATE TABLE IF NOT EXISTS audit_log (
    id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    project_id TEXT,
    action TEXT NOT NULL,
    detail TEXT,
    user_identity TEXT DEFAULT 'shared-user'
);
"""


def get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_conn()
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()


def now() -> str:
    return datetime.datetime.utcnow().isoformat()


def new_id() -> str:
    return str(uuid.uuid4())


# ---------- Projects ----------

def create_project(name: str, agency: str = "") -> str:
    pid = new_id()
    conn = get_conn()
    conn.execute(
        "INSERT INTO projects (id, name, agency, status, created_at, updated_at, duration_months) "
        "VALUES (?, ?, ?, 'Analyzing', ?, ?, 9)",
        (pid, name, agency, now(), now()),
    )
    conn.commit()
    conn.close()
    return pid


def get_project(project_id: str) -> dict | None:
    conn = get_conn()
    row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def list_projects(include_deleted: bool = False) -> list[dict]:
    conn = get_conn()
    if include_deleted:
        rows = conn.execute("SELECT * FROM projects ORDER BY created_at DESC").fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM projects WHERE deleted_at IS NULL ORDER BY created_at DESC"
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_project(project_id: str, **fields):
    if not fields:
        return
    fields["updated_at"] = now()
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [project_id]
    conn = get_conn()
    conn.execute(f"UPDATE projects SET {set_clause} WHERE id = ?", values)
    conn.commit()
    conn.close()


def soft_delete_project(project_id: str):
    update_project(project_id, deleted_at=now())


def restore_project(project_id: str):
    update_project(project_id, deleted_at=None)


# ---------- Documents ----------

def add_document(project_id: str, doc_type: str, filename: str, storage_path: str) -> str:
    doc_id = new_id()
    conn = get_conn()
    conn.execute(
        "INSERT INTO documents (id, project_id, doc_type, filename, storage_path, uploaded_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (doc_id, project_id, doc_type, filename, storage_path, now()),
    )
    conn.commit()
    conn.close()
    return doc_id


def list_documents(project_id: str) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM documents WHERE project_id = ? ORDER BY uploaded_at DESC", (project_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_document(doc_id: str) -> dict | None:
    conn = get_conn()
    row = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


# ---------- Audit log ----------

def log_action(action: str, project_id: str | None = None, detail: dict | str | None = None):
    if isinstance(detail, dict):
        detail = json.dumps(detail)
    conn = get_conn()
    conn.execute(
        "INSERT INTO audit_log (id, timestamp, project_id, action, detail) VALUES (?, ?, ?, ?, ?)",
        (new_id(), now(), project_id, action, detail),
    )
    conn.commit()
    conn.close()


def list_audit_log(project_id: str | None = None, limit: int = 500) -> list[dict]:
    conn = get_conn()
    if project_id:
        rows = conn.execute(
            "SELECT * FROM audit_log WHERE project_id = ? ORDER BY timestamp DESC LIMIT ?",
            (project_id, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM audit_log ORDER BY timestamp DESC LIMIT ?", (limit,)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
