"""
Postgres data layer (targets Supabase's managed Postgres, but works against any Postgres
instance — Supabase is just a connection string). Deliberately plain psycopg2 (no ORM); all
access goes through these functions so callers never see SQL or connection details.

Originally this module was plain sqlite3 against a single local file. That worked for a
single-instance deployment but meant the database lived on Render's local disk: no automated
backups, no dashboard to inspect data, and no safe way to run more than one app instance. This
version moves the same schema to Postgres so the database lives in Supabase instead — see
README for how to create a Supabase project and set DATABASE_URL.

Connection handling: a small pool (psycopg2.pool.ThreadedConnectionPool) is kept open for the
life of the process, since opening a fresh TCP+TLS connection to a remote Postgres host on
every call (the way the old sqlite3 code opened/closed a local file handle per call) would add
real latency. get_conn() borrows a connection from the pool and returns a thin wrapper whose
.execute()/.commit()/.close() match the old sqlite3.Connection interface, so every calling
function below is unchanged from the sqlite version except for the "?" -> "%s" placeholder
style. .close() returns the connection to the pool rather than actually closing the socket.
"""
from __future__ import annotations

import datetime
import json
import os
import uuid

import psycopg2
import psycopg2.extras
import psycopg2.pool

# Supabase's project settings page (Settings -> Database -> Connection string) gives you this
# URL directly — it already includes sslmode=require. Also accepts the plain Postgres-standard
# name (DATABASE_URL) so this isn't Supabase-specific if you ever point it elsewhere.
DATABASE_URL = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")

if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL (or SUPABASE_DB_URL) is not set. Create a Supabase project, copy its "
        "Postgres connection string from Settings -> Database -> Connection string, and set it "
        "as an environment variable. See README for the full setup steps."
    )

VALID_STATUSES = [
    "Analyzing",
    "Clarifications Sent",
    "Responses Pending",
    "Ready to Generate",
    "Submitted",
    "Declined",
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

# Columns added after the initial release — applied for databases created before this column
# existed, so upgrading in place never breaks on a missing column.
_MIGRATIONS = [
    ("projects", "capability_fit_json", "TEXT"),
    ("projects", "created_by", "TEXT"),
    ("documents", "encrypted", "INTEGER NOT NULL DEFAULT 0"),
]

_pool: psycopg2.pool.ThreadedConnectionPool | None = None


def _get_pool() -> psycopg2.pool.ThreadedConnectionPool:
    global _pool
    if _pool is None:
        _pool = psycopg2.pool.ThreadedConnectionPool(1, 10, dsn=DATABASE_URL)
    return _pool


class _Conn:
    """Wraps a pooled psycopg2 connection so calling code can keep using the same
    conn.execute(sql, params).fetchone()/.fetchall() / conn.commit() / conn.close() shape it
    used against sqlite3.Connection. Rows come back as RealDictRow (dict-like — row["col"] and
    dict(row) both work, matching how sqlite3.Row was used everywhere below)."""

    def __init__(self, raw):
        self._raw = raw

    def execute(self, sql: str, params=()):
        cur = self._raw.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        # Every query in this module was written with sqlite's "?" placeholder style; none of
        # them contain a literal "?" character anywhere else, so this blanket swap to
        # psycopg2's "%s" style is safe and avoids rewriting every call site individually.
        cur.execute(sql.replace("?", "%s"), params)
        return cur

    def executescript(self, script: str):
        cur = self._raw.cursor()
        cur.execute(script)
        cur.close()

    def commit(self):
        self._raw.commit()

    def rollback(self):
        self._raw.rollback()

    def close(self):
        """Returns the connection to the pool rather than closing the socket — matches the
        call sites below, which all call conn.close() once they're done with a request."""
        try:
            _get_pool().putconn(self._raw)
        except Exception:
            pass


def get_conn() -> _Conn:
    raw = _get_pool().getconn()
    return _Conn(raw)


def _apply_migrations(conn: _Conn):
    for table, column, coltype in _MIGRATIONS:
        cols = {
            row["column_name"]
            for row in conn.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = ?",
                (table,),
            ).fetchall()
        }
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")


def init_db():
    conn = get_conn()
    try:
        conn.executescript(SCHEMA)
        _apply_migrations(conn)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
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


def delete_user(user_id: str):
    """Permanently removes a user account. Safe to hard-delete (rather than soft-delete like
    projects) because nothing else references users.id as a foreign key — projects.created_by
    and audit_log.user_identity both store the username as a plain string at the time of the
    action, so history stays readable even after the account is gone."""
    conn = get_conn()
    conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()


def count_active_admins() -> int:
    conn = get_conn()
    n = conn.execute(
        "SELECT COUNT(*) AS n FROM users WHERE role = 'admin' AND is_active = 1"
    ).fetchone()["n"]
    conn.close()
    return n


def set_user_password(user_id: str, password_hash: str):
    conn = get_conn()
    conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, user_id))
    conn.commit()
    conn.close()
