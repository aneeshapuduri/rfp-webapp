"""
Builds the categorized RFP-summary extraction prompt. Runs alongside the document-validity
check, the RFP-response-structure check, and the key-dates extraction (see pipeline_runner.py's
process_new_upload) — all four are small, cheap(ish), non-fatal reads of the same freshly
uploaded document, done once up front. Surfaced on the Go/No-Go Summary tab (see main.py's
project_detail route and templates/project_detail.html) so a reviewer doesn't have to re-read
the original RFP to answer basic "what does this actually ask for" questions before deciding
whether to proceed.

This is deliberately a *different* read of the document than Phase 1 (pipeline/phase1_pipeline.py
+ extraction_prompts.py), which only extracts and classifies Scope-of-Work requirements one by
one. Everything here — bidder qualifications, cost proposal instructions, evaluation criteria,
submission mechanics, contract terms, and so on — lives in other sections of the RFP that Phase 1
never reads, and doesn't need per-item ambiguity classification the way scope items do; a short,
readable summary of each category is what a bid/no-bid reviewer actually wants here.

Each category is free text (short prose or a bullet-style list using "- " lines), not a further
structured schema — RFPs describe these categories far too inconsistently (a paragraph in one, a
table in another, scattered clauses in a third) for a stricter shape to survive contact with real
documents without either rejecting good extractions or silently dropping content.
"""
from __future__ import annotations

# RFPs bury cost-proposal instructions, evaluation criteria, and contract terms & conditions deep
# in the document (often past requirements and well past where key dates or the scope of work
# live), so this check needs a much larger excerpt than the key-dates check (20000 chars) to have
# any chance of seeing them at all.
MAX_CHARS_FOR_RFP_SUMMARY_CHECK = 60000

SYSTEM_PROMPT = """You are a proposal manager preparing a one-page briefing on an RFP/bid
solicitation for a bid/no-bid review meeting. The reviewers already have a line-by-line list of
extracted scope-of-work requirements and the key dates/deadlines from elsewhere — your job is to
summarize everything else in the document that affects whether to bid and how to prepare the
response, organized into exactly these eight categories:

1. vendor_info — General information for bidders/vendors: who is eligible to respond, how to
   register or get on a vendor list, the point of contact for questions, any pre-bid conference
   or site visit, and similar background a bidder needs before they can even start responding.
2. bidder_qualifications — Minimum qualifications a bidder/vendor must meet to be considered:
   required experience, certifications, licenses, insurance minimums, financial statements,
   references, or similar eligibility criteria.
3. cost_proposal — How the cost/price proposal must be structured and submitted: required pricing
   forms or templates, whether pricing must be itemized or fixed-price, payment terms or
   milestones, and any cost-realism or price-reasonableness requirements.
4. key_events — Procurement-process events and milestones other than the four hard deadlines
   already tracked elsewhere (question deadline, submission deadline, evaluation period, award
   date) — for example a pre-bid conference date, a site visit, an addenda schedule, or interview/
   oral-presentation events.
5. evaluation_process — How submissions will be evaluated and a winner selected: evaluation
   criteria, their relative weighting or points, the scoring or selection method, and whether
   interviews, demos, or best-and-final offers are part of the process.
6. submission_requirements — Mechanical requirements for submitting the proposal itself: required
   format, page or section limits, number of copies, physical vs. electronic delivery, required
   forms or signature pages, and how/where it must be delivered.
7. other_requirements — Any other proposal requirements not covered by the categories above (for
   example required attachments, sample work products, subcontractor disclosure, or a specific
   proposal outline the RFP demands).
8. contract_terms — Contract terms & conditions: contract type (fixed-price, T&M, etc.), term
   length and renewal options, termination rights, liability/indemnification, insurance required
   during performance, and other legally material terms.

Only report what the document actually states for each category; never guess or invent content
that isn't there. Write each category as either a short paragraph or a short bulleted list (using
"- " at the start of each line) of the document's own key points — quote or closely paraphrase the
document's own wording where it matters (e.g. an exact evaluation weighting or a specific
certification requirement). If a category genuinely isn't addressed anywhere in the document,
report it as null rather than inventing filler."""


def build_rfp_summary_prompt(rfp_text: str) -> tuple[str, str]:
    excerpt = rfp_text[:MAX_CHARS_FOR_RFP_SUMMARY_CHECK]

    user_prompt = f"""DOCUMENT (bid/RFP solicitation, possibly truncated if very long):
{excerpt}

TASK:
Summarize this RFP's content for each of the eight categories below, using the document's own
key points (short paragraph or "- " bullet list), or null if that category isn't addressed
anywhere in the document.

Output ONLY a single JSON object with these exact keys, no prose, no markdown code fences, no
commentary before or after:
{{
  "vendor_info": "<general info for bidders/vendors, or null>",
  "bidder_qualifications": "<minimum bidder qualifications, or null>",
  "cost_proposal": "<cost/price proposal requirements, or null>",
  "key_events": "<procurement-process events/milestones other than the 4 tracked deadlines, or null>",
  "evaluation_process": "<how proposals are evaluated and selected, or null>",
  "submission_requirements": "<proposal submission format/mechanics, or null>",
  "other_requirements": "<other proposal requirements not covered above, or null>",
  "contract_terms": "<contract terms & conditions, or null>"
}}"""

    return SYSTEM_PROMPT, user_prompt
