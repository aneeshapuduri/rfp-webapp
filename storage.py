"""
File storage for uploaded and generated documents. If ENCRYPTION_KEY is set in the
environment, files are encrypted at rest with Fernet (symmetric, authenticated encryption)
and transparently decrypted on read — satisfying the NFR spec's "encrypted at rest"
requirement without the caller needing to know or care. If no key is set, files are stored
as plain bytes and a warning is logged once at startup — see README for how to generate and
set a production key.

Security notes (from the code review this module was hardened after):
  - Disk filenames are NEVER derived from user-controlled input. `safe_stored_name()` validates
    the original filename's extension against a whitelist and returns a fresh, random,
    server-generated name to actually write to disk — a filename like '../../etc/passwd' or
    an absolute path can no longer escape the intended directory, because none of it reaches
    the filesystem path at all. The original filename is kept only as a display label in the
    database (rendered through Jinja's autoescaping, so it's also safe to show).
  - Whether a given file is encrypted is recorded per-document in the database (see db.py's
    `documents.encrypted` column) rather than inferred from whether ENCRYPTION_KEY happens to
    be set *right now*. That's what makes it safe to turn encryption on after some files were
    already stored unencrypted: old rows are read back as plaintext (encrypted=0), new rows as
    ciphertext (encrypted=1), and nothing crashes trying to Fernet-decrypt a plaintext file.
  - Callers should prefer `read_file_bytes(storage_path, encrypted)` and pass the per-document
    flag from the database. No plaintext "working copy" is ever written to disk for the
    pipeline to read — see pipeline_runner.py, which decrypts straight into memory.
"""
from __future__ import annotations

import os
import pathlib
import re
import uuid

# Same DATA_DIR env var as db.py (kept independent/duplicated on purpose — these two modules
# have no import dependency on each other today, and this is one line). Point it at a mounted
# persistent disk on platforms whose local filesystem doesn't survive a redeploy or restart.
DATA_DIR = pathlib.Path(os.environ.get("DATA_DIR") or (pathlib.Path(__file__).resolve().parent / "data"))
UPLOADS_DIR = DATA_DIR / "uploads"
GENERATED_DIR = DATA_DIR / "generated"

# Extensions this app ever needs to write or read. Anything else is rejected before it
# touches the filesystem.
ALLOWED_UPLOAD_EXTENSIONS = {".pdf", ".docx", ".txt"}
ALLOWED_GENERATED_EXTENSIONS = {".docx", ".json"}

MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MB — generous for an RFP + attachments, not unbounded

_fernet = None
_encryption_enabled = False


class UnsupportedFileType(ValueError):
    pass


class UploadTooLarge(ValueError):
    pass


def _get_fernet():
    global _fernet, _encryption_enabled
    if _fernet is not None:
        return _fernet
    import os
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
    what decides the on-disk path (that's always a fresh generated_name(), see below)."""
    name = pathlib.PurePosixPath(pathlib.PureWindowsPath(original_filename or "").name).name
    name = _SAFE_DISPLAY_NAME.sub("_", name).strip(" ._") or "file"
    return name[:200]


def generated_name(extension: str) -> str:
    """A random, collision-proof, purely-server-generated filename for disk storage."""
    return f"{uuid.uuid4().hex}{extension}"


def save_bytes(subdir: pathlib.Path, disk_filename: str, content: bytes) -> tuple[str, bool]:
    """Writes `content` under `subdir/disk_filename`, encrypting it if a key is configured.
    Returns (storage_path, encrypted) — the caller stores both in the documents table."""
    subdir.mkdir(parents=True, exist_ok=True)
    path = subdir / disk_filename
    fernet = _get_fernet()
    data = fernet.encrypt(content) if fernet else content
    path.write_bytes(data)
    return str(path), fernet is not None


def read_file_bytes(storage_path: str, encrypted: bool) -> bytes:
    """Reads a stored file, decrypting it only if it was actually stored encrypted — driven by
    the per-document flag from the database, not by whether ENCRYPTION_KEY happens to be set
    on this process right now. This is what makes enabling encryption after go-live safe: old
    plaintext documents keep reading as plaintext instead of raising InvalidToken."""
    data = pathlib.Path(storage_path).read_bytes()
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
    """Validates size + extension, then stores the upload under a random disk filename.
    Returns (display_filename, storage_path, encrypted)."""
    if len(content) > MAX_UPLOAD_BYTES:
        raise UploadTooLarge(f"File is larger than the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit.")
    ext = validate_extension(original_filename, ALLOWED_UPLOAD_EXTENSIONS)
    disk_name = generated_name(ext)
    storage_path, encrypted = save_bytes(UPLOADS_DIR / project_id, disk_name, content)
    return sanitize_display_name(original_filename), storage_path, encrypted


def save_generated(project_id: str, display_filename: str, content: bytes) -> tuple[str, bool]:
    """For app-generated documents (proposal, clarification questions) — the display filename
    is app-controlled, not user input, but we still never let it dictate the disk path."""
    ext = pathlib.Path(display_filename).suffix.lower()
    disk_name = generated_name(ext if ext in ALLOWED_GENERATED_EXTENSIONS else ".bin")
    return save_bytes(GENERATED_DIR / project_id, disk_name, content)
