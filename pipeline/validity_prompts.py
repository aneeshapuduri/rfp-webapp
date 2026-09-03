"""
Builds the pre-Phase-1 document validity check prompt. This runs before any requirement
extraction happens, so it deliberately only looks at the opening portion of the uploaded
document — a genuine bid/RFP solicitation almost always announces itself (issuing agency,
a scope of work, a submission deadline, proposal instructions) in its first page or two, and
keeping this prompt small keeps the check cheap and keeps noise from deep in a long document
from confusing the verdict.
"""
from __future__ import annotations

MAX_CHARS_FOR_VALIDITY_CHECK = 6000

SYSTEM_PROMPT = """You are a document intake specialist for a company that responds to
government and enterprise Request for Proposal (RFP) / bid solicitations. Before any analysis
work begins, you screen each uploaded document to confirm it is actually a bid/RFP solicitation
document, not some unrelated file that was uploaded by mistake (a random report, an internal
memo, an invoice, a resume, a news article, lorem-ipsum text, etc.). You look for solicitation
hallmarks: an issuing agency/organization, a scope of work or statement of work, a proposal or
bid submission deadline, submission/format instructions, and evaluation criteria. A document
does not need every hallmark to qualify — bid documents vary widely in format — but it should
read as a genuine solicitation seeking vendor proposals, not as some other kind of document
entirely. You are conservative: when a document is a genuine, if unusual or incomplete, bid or
RFP, you say so. You only flag a document as not a bid document when it clearly is not one."""


def build_validity_prompt(rfp_text: str) -> tuple[str, str]:
    excerpt = rfp_text[:MAX_CHARS_FOR_VALIDITY_CHECK]

    user_prompt = f"""DOCUMENT EXCERPT (opening portion of the uploaded file):
{excerpt}

TASK:
Determine whether this document is a bid/RFP solicitation document (a request for proposals,
request for quotes, invitation to bid, or similar solicitation seeking vendor responses).

Output ONLY a single JSON object with these exact keys, no prose, no markdown code fences, no
commentary before or after:
{{
  "is_bid_document": true or false,
  "confidence": "high", "medium", or "low",
  "reasoning": "one or two sentences explaining the verdict, citing what you did or did not find"
}}"""

    return SYSTEM_PROMPT, user_prompt
