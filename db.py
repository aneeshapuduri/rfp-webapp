"""
SQLite data layer. Deliberately plain sqlite3 (no ORM) to keep the deployment footprint
small — one file, no extra service to run. Swap for Postgres later without touching callers
much, since all access goes through these functions.
"""
from __future__ import annotations

import datetime
import json
import os
import pathlib
import sqlite3
import uuid

# DATA_DIR is configurable so a deployment on a platform with an ephemeral filesystem (Render,
# Heroku, most container platforms) can point this at a mounted persistent disk instead of the
# app's own directory, which gets wiped on every redeploy. Defaults to the old behavior
# (a 'data' folder next to the app) for local/VPS use where the app directory itself persists.
DATA_DIR = pathlib.Path(os.environ.get("DATA_DIR") or (pathlib.Path(__file__).resolve().parent / "data"))
DB_PATH = DATA_DIR / "app.db"

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
    capability_fit_json TEXT,
    duration_months REAL DEFAULT 9,
    created_by TEXT
);

CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    doc_type TEXT NOT NULL,   -- 'bid_invitation' | 'clarification_questions' | 'client_responses' | 'final_proposal'
    filename TEXT NOT NULL,
    storage_path TEXT NOT NULL,
    uploaded_at TEXT NOT NULL,
    encrypted INTEGER NOT NULL DEFAULT 0,
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

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'member',   -- 'admin' | 'member'
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    created_by TEXT
);
"""

# Columns added after the initial release — applied with ALTER TABLE for databases created
# before this column existed, so upgrading in place never breaks on a missing column.
_MIGRATIONS = [
    ("projects", "capability_fit_json", "TEXT"),
    ("projects", "created_by", "TEXT"),
    ("documents", "encrypted", "INTEGER NOT NULL DEFAULT 0"),
]


def get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _apply_migrations(conn: sqlite3.Connection):
    for table, column, coltype in _MIGRATIONS:
        cols = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")


def init_db():
    conn = get_conn()
    conn.executescript(SCHEMA)
    _apply_migrations(conn)
    conn.commit()
    conn.close()


def now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def new_id() -> str:
    return str(uuid.uuid4())


# ---------- Projects ----------

def create_project(name: str, agency: str = "", created_by: str | None = None) -> str:
    pid = new_id()
    conn = get_conn()
    conn.execute(
        "INSERT INTO projects (id, name, agency, status, created_at, updated_at, duration_months, created_by) "
        "VALUES (?, ?, ?, 'Analyzing', ?, ?, 9, ?)",
        (pid, name, agency, now(), now(), created_by),
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

def add_document(project_id: str, doc_type: str, filename: str, storage_path: str, encrypted: bool = False) -> str:
    doc_id = new_id()
    conn = get_conn()
    conn.execute(
        "INSERT INTO documents (id, project_id, doc_type, filename, storage_path, uploaded_at, encrypted) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (doc_id, project_id, doc_type, filename, storage_path, now(), 1 if encrypted else 0),
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

def log_action(action: str, project_id: str | None = None, detail: dict | str | None = None,
               user_identity: str = "system"):
    if isinstance(detail, dict):
        detail = json.dumps(detail)
    conn = get_conn()
    conn.execute(
        "INSERT INTO audit_log (id, timestamp, project_id, action, detail, user_identity) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (new_id(), now(), project_id, action, detail, user_identity),
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


# ---------- Users ----------
# Passwords are never stored in plaintext — see auth.py for hashing. This module only ever
# stores/compares the resulting hash string.

def create_user(username: str, password_hash: str, role: str = "member", created_by: str | None = None) -> str:
    uid = new_id()
    conn = get_conn()
    conn.execute(
        "INSERT INTO users (id, username, password_hash, role, is_active, created_at, created_by) "
        "VALUES (?, ?, ?, ?, 1, ?, ?)",
        (uid, username.strip().lower(), password_hash, role, now(), created_by),
    )
    conn.commit()
    conn.close()
    return uid


def get_user_by_username(username: str) -> dict | None:
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM users WHERE username = ?", (username.strip().lower(),)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_user(user_id: str) -> dict | None:
    conn = get_conn()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def list_users() -> list[dict]:
    conn = get_conn()
    rows = conn.execute("SELECT * FROM users ORDER BY created_at ASC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def count_users() -> int:
    conn = get_conn()
    n = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
    conn.close()
    return n


def set_user_active(user_id: str, is_active: bool):
    conn = get_conn()
    conn.execute("UPDATE users SET is_active = ? WHERE id = ?", (1 if is_active else 0, user_id))
    conn.commit()
    conn.close()


def set_user_password(user_id: str, password_hash: str):
    conn = get_conn()
    conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, user_id))
    conn.commit()
    conn.close()
