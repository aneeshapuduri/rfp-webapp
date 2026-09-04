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
    "Not a Bid Document",
    "Clarifications Sent",
    "Responses Pending",
    "Awaiting Assumptions Approval",
    "Awaiting Preview",
    "Ready to Generate",
    "Submitted",
    "Declined",
    "Cancelled",
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

-- Per-member project visibility. Admins are never restricted (checked in application code, not
-- here) — a row here only ever matters for a 'member' user. A member's accessible set is the
-- union of these grants plus any project they personally created (projects.created_by), which
-- is why there's no need to auto-insert a row here when a member creates a project themselves.
CREATE TABLE IF NOT EXISTS project_access (
    user_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    granted_at TEXT NOT NULL,
    granted_by TEXT,
    PRIMARY KEY (user_id, project_id)
);
"""

# Columns added after the initial release — applied for databases created before this column
# existed, so upgrading in place never breaks on a missing column.
_MIGRATIONS = [
    ("projects", "capability_fit_json", "TEXT"),
    ("projects", "created_by", "TEXT"),
    ("documents", "encrypted", "INTEGER NOT NULL DEFAULT 0"),
    ("projects", "template_choice", "TEXT"),
    ("projects", "validity_rejection_reason", "TEXT"),
    # Staged, not-yet-applied client answers — populated when a filled-in clarification doc is
    # uploaded, so its answers can pre-fill the per-question text boxes on the project page for
    # review/editing before the user explicitly submits them (see main.py's upload_responses and
    # submit_client_responses routes). Cleared once submitted.
    ("projects", "pending_client_responses_json", "TEXT"),
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


def _table_exists(conn: _Conn, table: str) -> bool:
    row = conn.execute(
        "SELECT to_regclass(?) AS reg", (table,)
    ).fetchone()
    return row["reg"] is not None


def _grandfather_project_access(conn: _Conn):
    """Runs exactly once — only right after project_access is created for the first time (see
    init_db below). Every existing member gets access to every existing non-deleted project, so
    nobody who could already see a project loses access the moment this feature ships. Projects
    created after this point are NOT retroactively granted to old members — only to whichever
    members an admin explicitly grants them to (or the member who creates them)."""
    conn.execute(
        "INSERT INTO project_access (user_id, project_id, granted_at, granted_by) "
        "SELECT u.id, p.id, ?, 'system_grandfather' "
        "FROM users u CROSS JOIN projects p "
        "WHERE u.role = 'member' AND p.deleted_at IS NULL "
        "ON CONFLICT (user_id, project_id) DO NOTHING",
        (now(),),
    )


def init_db():
    conn = get_conn()
    try:
        project_access_existed = _table_exists(conn, "project_access")
        conn.executescript(SCHEMA)
        _apply_migrations(conn)
        if not project_access_existed:
            _grandfather_project_access(conn)
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


# ---------- Project access ----------
# Admins are always unrestricted — that's checked in application code (user["role"] == "admin"),
# never here. These functions only ever matter for filtering what a 'member' user can reach.

def grant_project_access(user_id: str, project_id: str, granted_by: str | None = None):
    conn = get_conn()
    conn.execute(
        "INSERT INTO project_access (user_id, project_id, granted_at, granted_by) "
        "VALUES (?, ?, ?, ?) ON CONFLICT (user_id, project_id) DO NOTHING",
        (user_id, project_id, now(), granted_by),
    )
    conn.commit()
    conn.close()


def revoke_project_access(user_id: str, project_id: str):
    conn = get_conn()
    conn.execute(
        "DELETE FROM project_access WHERE user_id = ? AND project_id = ?", (user_id, project_id)
    )
    conn.commit()
    conn.close()


def set_user_project_access(user_id: str, project_ids: list[str], granted_by: str | None = None):
    """Replaces the full set of explicit grants for a user in one go — used both when an admin
    creates a member with an initial project list, and when editing an existing member's access
    afterward."""
    conn = get_conn()
    conn.execute("DELETE FROM project_access WHERE user_id = ?", (user_id,))
    ts = now()
    for pid in project_ids:
        conn.execute(
            "INSERT INTO project_access (user_id, project_id, granted_at, granted_by) "
            "VALUES (?, ?, ?, ?) ON CONFLICT (user_id, project_id) DO NOTHING",
            (user_id, pid, ts, granted_by),
        )
    conn.commit()
    conn.close()


def list_accessible_project_ids(user_id: str) -> set[str]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT project_id FROM project_access WHERE user_id = ?", (user_id,)
    ).fetchall()
    conn.close()
    return {r["project_id"] for r in rows}


def user_can_access_project(user: dict, project: dict, accessible_ids: set[str] | None = None) -> bool:
    if user["role"] == "admin":
        return True
    if project.get("created_by") == user["username"]:
        return True
    ids = accessible_ids if accessible_ids is not None else list_accessible_project_ids(user["id"])
    return project["id"] in ids


def list_projects_for_user(user: dict, include_deleted: bool = False) -> list[dict]:
    if user["role"] == "admin":
        return list_projects(include_deleted)
    conn = get_conn()
    sql = (
        "SELECT * FROM projects WHERE "
        "(id IN (SELECT project_id FROM project_access WHERE user_id = ?) OR created_by = ?)"
    )
    params = [user["id"], user["username"]]
    if not include_deleted:
        sql += " AND deleted_at IS NULL"
    sql += " ORDER BY created_at DESC"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


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


def _system_filter_clause(filter_mode: str) -> str:
    """log_action() attributes every automated pipeline/app event to user_identity='system'
    (the default when no real user triggered it) and every human action to a real username —
    so this one comparison is all "system logs" vs "user logs" ever needs to mean."""
    if filter_mode == "user":
        return "user_identity != 'system'"
    if filter_mode == "system":
        return "user_identity = 'system'"
    return ""


def list_audit_log(project_id: str | None = None, limit: int = 500, filter_mode: str = "all") -> list[dict]:
    conn = get_conn()
    conditions = []
    params: list = []
    if project_id:
        conditions.append("project_id = ?")
        params.append(project_id)
    sys_clause = _system_filter_clause(filter_mode)
    if sys_clause:
        conditions.append(sys_clause)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    rows = conn.execute(
        f"SELECT * FROM audit_log {where} ORDER BY timestamp DESC LIMIT ?", (*params, limit)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def list_audit_log_for_user(user: dict, project_id: str | None = None, limit: int = 500,
                             filter_mode: str = "all") -> list[dict]:
    """Same as list_audit_log, but a non-admin only sees entries for projects they can access,
    plus entries with no project (e.g. their own login activity) that are attributed to them.
    filter_mode: "all" (default), "user" (real people only, for the Home page feed and the
    Audit Log's User tab), or "system" (automated pipeline/app events, for the System tab)."""
    if user["role"] == "admin":
        return list_audit_log(project_id, limit, filter_mode)
    conn = get_conn()
    accessible = list_accessible_project_ids(user["id"])
    created = {
        r["id"] for r in conn.execute(
            "SELECT id FROM projects WHERE created_by = ?", (user["username"],)
        ).fetchall()
    }
    visible_ids = accessible | created
    sys_clause = _system_filter_clause(filter_mode)
    sys_and = f" AND {sys_clause}" if sys_clause else ""

    if project_id:
        if project_id not in visible_ids:
            conn.close()
            return []
        rows = conn.execute(
            f"SELECT * FROM audit_log WHERE project_id = ?{sys_and} ORDER BY timestamp DESC LIMIT ?",
            (project_id, limit),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    if visible_ids:
        placeholders = ", ".join("?" for _ in visible_ids)
        rows = conn.execute(
            f"SELECT * FROM audit_log WHERE (project_id IN ({placeholders}) "
            f"OR (project_id IS NULL AND user_identity = ?)){sys_and} "
            "ORDER BY timestamp DESC LIMIT ?",
            (*visible_ids, user["username"], limit),
        ).fetchall()
    else:
        rows = conn.execute(
            f"SELECT * FROM audit_log WHERE project_id IS NULL AND user_identity = ?{sys_and} "
            "ORDER BY timestamp DESC LIMIT ?",
            (user["username"], limit),
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
