"""
Builds the key-dates extraction prompt. Runs alongside the document-validity check and the
RFP-response-structure check (see pipeline_runner.py's process_new_upload) — all three are
small, cheap, non-fatal checks over the same freshly-uploaded document, done once up front
rather than making the user hunt through the original RFP for deadlines. Extracted dates are
surfaced on the Go/No-Go Summary tab (see main.py's project_detail route and
templates/project_detail.html) alongside the compliance/requirements match table.

Dates are extracted as free text, not parsed into strict date objects — RFPs phrase these
inconsistently ("no later than 5:00 PM ET on March 3, 2027", "within 10 business days of
issuance", "Q2 2027"), and forcing a parse would either reject a lot of genuinely useful
information or silently misread it. Free text, shown to a human, is the safer choice here.
"""
from __future__ import annotations

MAX_CHARS_FOR_KEY_DATES_CHECK = 20000

SYSTEM_PROMPT = """You are a proposal manager reviewing an RFP/bid solicitation to extract its
key dates and deadlines — the ones a bid team needs to track to respond on time and know when to
expect a decision. You look for: the deadline for submitting questions or requesting
clarifications, the final proposal/bid submission deadline, the evaluation period (when the
issuing organization reviews submissions, sometimes including interviews or demos), and the
award/results date (when a decision or contract award is expected or announced). Not every RFP
states all four — many only give a submission deadline. Only report what the document actually
states; never guess or infer a date that isn't there. Quote or closely paraphrase the document's
own wording for each date you find (including its own phrasing of time, timezone, and any
conditions), since exact wording matters for compliance."""


def build_key_dates_prompt(rfp_text: str) -> tuple[str, str]:
    excerpt = rfp_text[:MAX_CHARS_FOR_KEY_DATES_CHECK]

    user_prompt = f"""DOCUMENT (bid/RFP solicitation, possibly truncated if very long):
{excerpt}

TASK:
Extract this RFP's key dates, if stated. For each of the four categories below, give the
document's own date/deadline text (or null if that category isn't addressed anywhere in the
document).

Output ONLY a single JSON object with these exact keys, no prose, no markdown code fences, no
commentary before or after:
{{
  "question_deadline": "<the question/clarification submission deadline, in the document's own words, or null>",
  "submission_deadline": "<the final proposal/bid submission deadline, in the document's own words, or null>",
  "evaluation_period": "<the evaluation/review period or process, in the document's own words, or null>",
  "award_date": "<the expected award/results/decision date, in the document's own words, or null>"
}}"""

    return SYSTEM_PROMPT, user_prompt
