"""
Phase 2: reads a filled-in Clarification Questions document (client sent it back with the
Answer column completed) and matches each answer back to its requirement_id using the sidecar
mapping file written by clarification_doc_builder.py.
"""
from __future__ import annotations

import json
import pathlib

import docx


def read_client_responses(filled_docx_path: str, mapping_path: str) -> dict[str, str]:
    """
    Returns {requirement_id: client_answer_text}. Skips rows where the Answer column is still
    blank (client didn't answer that one) rather than guessing.
    """
    mapping = json.loads(pathlib.Path(mapping_path).read_text(encoding="utf-8"))

    d = docx.Document(filled_docx_path)
    if not d.tables:
        raise ValueError("No table found in the filled document — expected the Q&A table.")
    table = d.tables[0]

    responses: dict[str, str] = {}
    for row in table.rows[1:]:  # skip header
        cells = [c.text.strip() for c in row.cells]
        if len(cells) < 3:
            continue
        q_num, _question, answer = cells[0], cells[1], cells[2]
        if not answer:
            continue
        req_id = mapping.get(q_num)
        if req_id:
            responses[req_id] = answer

    return responses
