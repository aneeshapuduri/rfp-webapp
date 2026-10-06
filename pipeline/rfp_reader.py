"""
Reads an RFP document (.txt, .docx, or .pdf) and returns its plain text content
so it can be fed into the section-generation prompts.
"""
import pathlib


def _require_text(text: str, scanned_hint: bool = False) -> str:
    """An empty extraction used to flow on into the LLM with a blank prompt (wasted spend and a
    confusing failure). Fail early with a message the uploader can act on."""
    if not text or not text.strip():
        hint = " Scanned/image-only PDFs aren't supported — upload a text-based copy." if scanned_hint else ""
        raise ValueError("No readable text was found in this document." + hint)
    return text


def read_rfp(path: str) -> str:
    p = pathlib.Path(path)
    if not p.exists():
        raise FileNotFoundError(f"RFP file not found: {path}")

    suffix = p.suffix.lower()

    if suffix == ".txt":
        raw = p.read_bytes()
        # Windows-authored .txt files are often cp1252, not UTF-8; a strict decode used to turn
        # such an upload into a 500-style pipeline failure.
        for enc in ("utf-8-sig", "cp1252"):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            text = raw.decode("utf-8", errors="replace")
        return _require_text(text)

    if suffix == ".docx":
        try:
            import docx  # python-docx
        except ImportError as e:
            raise RuntimeError("python-docx is required to read .docx files: pip install python-docx") from e
        try:
            d = docx.Document(str(p))
        except Exception as e:  # noqa: BLE001 - zip/xml errors vary
            raise ValueError("This file couldn't be opened as a Word document — it may be "
                             "corrupt or not a real .docx file.") from e
        parts = [para.text for para in d.paragraphs if para.text.strip()]
        for table in d.tables:
            for row in table.rows:
                parts.append(" | ".join(cell.text for cell in row.cells))
        return _require_text("\n".join(parts))

    if suffix == ".pdf":
        try:
            import pypdf
        except ImportError as e:
            raise RuntimeError("pypdf is required to read .pdf files: pip install pypdf") from e
        try:
            reader = pypdf.PdfReader(str(p))
            if reader.is_encrypted:
                # Many PDFs are "encrypted" with an empty user password purely to block
                # editing/printing; those open fine. A real password can't be supplied here.
                if not reader.decrypt(""):
                    raise ValueError("This PDF is password-protected. Remove the password and "
                                     "upload it again.")
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
        except ValueError:
            raise
        except Exception as e:  # noqa: BLE001 - pypdf raises many error types for bad files
            raise ValueError("This file couldn't be read as a PDF — it may be corrupt or not a "
                             "real PDF.") from e
        return _require_text(text, scanned_hint=True)

    raise ValueError(f"Unsupported RFP file type: {suffix}. Use .txt, .docx, or .pdf.")
