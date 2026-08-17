"""
Phase 2: given a client's answer to a clarification question, decide whether the requirement
is now clear enough to proceed, or whether the answer itself is too vague and needs a human
to review rather than looping another auto-generated question back to the client.
"""
from __future__ import annotations

SYSTEM_PROMPT = """You are a senior business analyst reviewing a client's answer to a
clarification question you asked about an RFP requirement. Decide whether the answer gives
enough detail for a solutions architect to now design and price against the requirement. Be
conservative: a vague, partial, or non-committal answer should NOT be marked resolved just to
keep the pipeline moving — an unresolved ambiguity reaching solution design risks a wrong
technical or pricing decision. Do not generate a follow-up question; if the answer is
insufficient, mark it for manual review instead."""


def build_resolution_prompt(
    requirement: str, original_question: str, client_answer: str
) -> tuple[str, str]:
    user_prompt = f"""ORIGINAL REQUIREMENT: {requirement}

CLARIFICATION QUESTION WE ASKED: {original_question}

CLIENT'S ANSWER: {client_answer}

Decide whether this answer resolves the ambiguity. Output ONLY a JSON object (no prose, no
markdown fences) with exactly these keys:
"resolved" (boolean — true only if a solutions architect could now confidently design and
price against this requirement using the answer given),
"updated_requirement" (the requirement restated to incorporate the new information, only if
resolved=true, else null),
"reasoning" (1-2 sentences explaining the resolved/not-resolved decision)."""
    return SYSTEM_PROMPT, user_prompt
