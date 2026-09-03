"""
File storage for uploaded and generated documents — backed by a Supabase Storage bucket.

Originally, files were written to local disk under DATA_DIR, same as the old SQLite database.
That tied document durability to Render's persistent disk: if the disk wasn't actually attached,
or the plan didn't support one, every uploaded bid invitation and generated proposal vanished on
the next restart — exactly the same failure mode that hit the database before it moved to
Supabase Postgres. This version stores the file bytes themselves in a Supabase Storage bucket,
so documents survive redeploys/restarts the same way project rows now do, and are browsable in
the Supabase dashboard's Storage section independent of the app. There is no local-disk fallback
and no persistent disk requirement left in this app at all after this change.

Security notes (from the code review this module was hardened after, still true here):
  - Disk/object names are NEVER derived from user-controlled input. `safe_stored_name()` (via
    `generated_name()`) validates the original filename's extension against a whitelist and
    returns a fresh, random, server-generated name to actually store — a filename like
    '../../etc/passwd' can no longer escape the intended object prefix, because none of it
    reaches the storage path at all. The original filename is kept only as a display label in
    the database (rendered through Jinja's autoescaping, so it's also safe to show).
  - Whether a given file is encrypted is recorded per-document in the database (see db.py's
    `documents.encrypted` column) rather than inferred from whether ENCRYPTION_KEY happens to
    be set *right now*. That's what makes it safe to turn encryption on after some files were
    already stored unencrypted: old rows are read back as plaintext (encrypted=0), new rows as
    ciphertext (encrypted=1), and nothing crashes trying to Fernet-decrypt a plaintext file.
  - Encryption happens entirely in this process, before a single byte leaves it — Supabase
    Storage only ever sees ciphertext when ENCRYPTION_KEY is set. The service_role key used to
    talk to Storage is a separate concern (it authorizes this backend to manage the bucket at
    all) and must be treated as a secret at least as sensitive as a database password: never
    logged, never sent to a template, never exposed to a browser.
  - Callers should prefer `read_file_bytes(storage_path, encrypted)` and pass the per-document
    flag from the database. No plaintext "working copy" is ever written to disk for the
    pipeline to read — see pipeline_runner.py, which decrypts straight into memory.
"""
from __future__ import annotations

import os
import pathlib
import re
import uuid

import requests

# Settings -> API in the Supabase dashboard gives you SUPABASE_URL and the service_role key.
# The service_role key (NOT the anon/public key) is required because this backend manages every
# project's files under its own login system, not Supabase's — service_role bypasses row-level
# security entirely, which is what lets a single backend process own every object in the bucket.
SUPABASE_URL = (os.environ.get("SUPABASE_URL") or "").rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or ""
SUPABASE_STORAGE_BUCKET = os.environ.get("SUPABASE_STORAGE_BUCKET") or "rfp-documents"

if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
    raise RuntimeError(
        "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must both be set — uploaded and generated "
        "documents are stored in a Supabase Storage bucket, there is no local-disk fallback. "
        "Get both from your Supabase project's Settings -> API page (use the service_role "
        "secret key, not the anon/public key). See README for the full setup steps."
    )

_STORAGE_API = f"{SUPABASE_URL}/storage/v1"
_AUTH_HEADERS = {
    "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
    "apikey": SUPABASE_SERVICE_ROLE_KEY,
}
_REQUEST_TIMEOUT = 30  # seconds — generous for a document-sized object over the internet

# Extensions this app ever needs to write or read. Anything else is rejected before it
# touches Storage.
ALLOWED_UPLOAD_EXTENSIONS = {".pdf", ".docx", ".txt"}
ALLOWED_GENERATED_EXTENSIONS = {".docx", ".json"}

MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MB — generous for an RFP + attachments, not unbounded

_fernet = None
_encryption_enabled = False


class UnsupportedFileType(ValueError):
    pass


class UploadTooLarge(ValueError):
    pass


class StorageError(RuntimeError):
    """Raised when Supabase Storage itself returns an error uploading, downloading, or
    provisioning the bucket — as distinct from an application-level validation error."""


def _get_fernet():
    global _fernet, _encryption_enabled
    if _fernet is not None:
        return _fernet
    key = os.environ.get("ENCRYPTION_KEY")
    if key:
        from cryptography.fernet import Fernet
        _fernet = Fernet(key.encode() if isinstance(key, str) else key)
        _encryption_enabled = True
    return _fernet


def is_encryption_enabled() -> bool:
    _get_fernet()
    return _encryption_enabled


def validate_extension(original_filename: str, allowed: set[str]) -> str:
    """Returns the lowercase extension if it's on the allow-list; raises UnsupportedFileType
    otherwise. Never trust the rest of the filename beyond this."""
    suffix = pathlib.Path(original_filename or "").suffix.lower()
    if suffix not in allowed:
        raise UnsupportedFileType(
            f"'{suffix or '(no extension)'}' is not an allowed file type. "
            f"Allowed: {', '.join(sorted(allowed))}"
        )
    return suffix


_SAFE_DISPLAY_NAME = re.compile(r"[^A-Za-z0-9 ._-]+")


def sanitize_display_name(original_filename: str) -> str:
    """Best-effort cleanup of the *display* filename (shown in the UI, stored in the DB) —
    strips any path components and odd characters. This is defense in depth only; it is NOT
    what decides the storage object path (that's always a fresh generated_name(), see below)."""
    name = pathlib.PurePosixPath(pathlib.PureWindowsPath(original_filename or "").name).name
    name = _SAFE_DISPLAY_NAME.sub("_", name).strip(" ._") or "file"
    return name[:200]


def generated_name(extension: str) -> str:
    """A random, collision-proof, purely-server-generated filename for the storage object."""
    return f"{uuid.uuid4().hex}{extension}"


def ensure_bucket_exists():
    """Called once at startup. Creates the configured bucket as private if it doesn't already
    exist. Cheap and safe to call on every restart — it's a no-op once the bucket is there."""
    resp = requests.get(
        f"{_STORAGE_API}/bucket/{SUPABASE_STORAGE_BUCKET}",
        headers=_AUTH_HEADERS,
        timeout=_REQUEST_TIMEOUT,
    )
    if resp.status_code == 200:
        return
    resp = requests.post(
        f"{_STORAGE_API}/bucket",
        headers={**_AUTH_HEADERS, "Content-Type": "application/json"},
        json={"id": SUPABASE_STORAGE_BUCKET, "name": SUPABASE_STORAGE_BUCKET, "public": False},
        timeout=_REQUEST_TIMEOUT,
    )
    if resp.status_code not in (200, 201):
        raise StorageError(
            f"Could not create the Supabase Storage bucket '{SUPABASE_STORAGE_BUCKET}' "
            f"({resp.status_code}): {resp.text[:300]}"
        )


def _upload_object(object_path: str, content: bytes):
    # Every object path this app ever writes to comes from generated_name() — a fresh
    # uuid4 hex on every call — so a collision (and therefore ever needing to overwrite an
    # existing object) is not a real possibility. Plain POST without x-upsert is deliberate:
    # it means an accidental re-upload to the same path fails loudly instead of silently
    # replacing another document's bytes.
    resp = requests.post(
        f"{_STORAGE_API}/object/{SUPABASE_STORAGE_BUCKET}/{object_path}",
        headers={**_AUTH_HEADERS, "Content-Type": "application/octet-stream"},
        data=content,
        timeout=_REQUEST_TIMEOUT,
    )
    if resp.status_code not in (200, 201):
        raise StorageError(
            f"Upload to Supabase Storage failed ({resp.status_code}): {resp.text[:300]}"
        )


def _download_object(object_path: str) -> bytes:
    resp = requests.get(
        f"{_STORAGE_API}/object/authenticated/{SUPABASE_STORAGE_BUCKET}/{object_path}",
        headers=_AUTH_HEADERS,
        timeout=_REQUEST_TIMEOUT,
    )
    if resp.status_code != 200:
        raise StorageError(
            f"Download from Supabase Storage failed ({resp.status_code}): {resp.text[:300]}"
        )
    return resp.content


def save_bytes(object_prefix: str, disk_filename: str, content: bytes) -> tuple[str, bool]:
    """Uploads `content` to Supabase Storage under `object_prefix/disk_filename`, encrypting it
    first if a key is configured. Returns (storage_path, encrypted) — the caller stores both in
    the documents table. `storage_path` is the object's path *within the bucket*, not a
    filesystem path."""
    object_path = f"{object_prefix}/{disk_filename}"
    fernet = _get_fernet()
    data = fernet.encrypt(content) if fernet else content
    _upload_object(object_path, data)
    return object_path, fernet is not None


def read_file_bytes(storage_path: str, encrypted: bool) -> bytes:
    """Reads a stored file from Supabase Storage, decrypting it only if it was actually stored
    encrypted — driven by the per-document flag from the database, not by whether
    ENCRYPTION_KEY happens to be set on this process right now. This is what makes enabling
    encryption after go-live safe: old plaintext documents keep reading as plaintext instead of
    raising InvalidToken."""
    data = _download_object(storage_path)
    if encrypted:
        fernet = _get_fernet()
        if fernet is None:
            raise RuntimeError(
                "This document was stored encrypted, but no ENCRYPTION_KEY is configured on "
                "this process — set the same ENCRYPTION_KEY used when it was stored."
            )
        return fernet.decrypt(data)
    return data


def save_upload(project_id: str, original_filename: str, content: bytes) -> tuple[str, str, bool]:
    """Validates size + extension, then stores the upload under a random object name.
    Returns (display_filename, storage_path, encrypted)."""
    if len(content) > MAX_UPLOAD_BYTES:
        raise UploadTooLarge(f"File is larger than the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit.")
    ext = validate_extension(original_filename, ALLOWED_UPLOAD_EXTENSIONS)
    disk_name = generated_name(ext)
    storage_path, encrypted = save_bytes(f"uploads/{project_id}", disk_name, content)
    return sanitize_display_name(original_filename), storage_path, encrypted


def save_generated(project_id: str, display_filename: str, content: bytes) -> tuple[str, bool]:
    """For app-generated documents (proposal, clarification questions) — the display filename
    is app-controlled, not user input, but we still never let it dictate the storage path."""
    ext = pathlib.Path(display_filename).suffix.lower()
    disk_name = generated_name(ext if ext in ALLOWED_GENERATED_EXTENSIONS else ".bin")
    return save_bytes(f"generated/{project_id}", disk_name, content)
