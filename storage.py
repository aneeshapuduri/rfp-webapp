"""
File storage for uploaded and generated documents. If ENCRYPTION_KEY is set in the
environment, files are encrypted at rest with Fernet (symmetric, authenticated encryption)
and transparently decrypted on read — satisfying the NFR spec's "encrypted at rest"
requirement without the caller needing to know or care. If no key is set, files are stored
as plain bytes and a warning is logged once at startup — see README for how to generate and
set a production key.
"""
from __future__ import annotations

import os
import pathlib

DATA_DIR = pathlib.Path(__file__).resolve().parent / "data"
UPLOADS_DIR = DATA_DIR / "uploads"
GENERATED_DIR = DATA_DIR / "generated"

_fernet = None
_encryption_enabled = False


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


def save_file(subdir: pathlib.Path, filename: str, content: bytes) -> str:
    subdir.mkdir(parents=True, exist_ok=True)
    path = subdir / filename
    fernet = _get_fernet()
    data = fernet.encrypt(content) if fernet else content
    path.write_bytes(data)
    return str(path)


def read_file(storage_path: str) -> bytes:
    data = pathlib.Path(storage_path).read_bytes()
    fernet = _get_fernet()
    if fernet:
        return fernet.decrypt(data)
    return data


def save_upload(project_id: str, filename: str, content: bytes) -> str:
    return save_file(UPLOADS_DIR / project_id, filename, content)


def save_generated(project_id: str, filename: str, content: bytes) -> str:
    return save_file(GENERATED_DIR / project_id, filename, content)
