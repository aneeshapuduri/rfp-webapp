"""
Builds the RFP-specified response-structure detection prompt. Some bid documents don't just
ask for a proposal — they dictate the exact section headings and order the vendor's response
must follow (often under a heading like "Proposal Format," "Instructions to Offerors," or
"Response Requirements"). When that's the case, the generated proposal should be organized
under the RFP's own section names instead of our standard 31-section template — see
template_mapper.py for how those detected names get fuzzy-matched to our canonical sections,
and document_builder.py's RFP-structure branch for how the final document gets built from them.

This runs alongside the existing document-validity check (see validity_prompts.py) — both look
at the same uploaded document before Phase 1 extraction begins. Response-structure instructions
can appear anywhere in a solicitation (sometimes buried past the scope of work), so this prompt
is given a larger excerpt than the validity check's — enough to reach past the front matter into
an "Instructions to Offerors" or "Proposal Submission Requirements" section without sending the
entire document (which, for a long RFP, would be an unnecessarily expensive call for a check
that only needs to find one section, if it exists at all).
"""
from __future__ import annotations

MAX_CHARS_FOR_STRUCTURE_CHECK = 20000

SYSTEM_PROMPT = """You are a proposal manager reviewing an RFP/bid solicitation to determine
whether it dictates a specific structure the vendor's WRITTEN RESPONSE must follow — i.e. an
explicit list of section headings, in a specific order, that the proposal document itself must
be organized under (often found under a heading like "Proposal Format," "Instructions to
Offerors," "Response Requirements," "Organization of the Proposal," or similar). This is
different from the RFP's scope of work, evaluation criteria, or requirements list — those
describe what the vendor must DO or PROVIDE, not how the vendor's response document must be
organized. Only report a structure when the RFP explicitly names response sections/headings the
proposal must include, in the order it wants them. Many RFPs do not specify this at all — in
that case, say so plainly rather than inventing a structure from the general shape of the
document."""


def build_response_structure_prompt(rfp_text: str) -> tuple[str, str]:
    excerpt = rfp_text[:MAX_CHARS_FOR_STRUCTURE_CHECK]

    user_prompt = f"""DOCUMENT (bid/RFP solicitation, possibly truncated if very long):
{excerpt}

TASK:
Determine whether this document explicitly specifies a required structure/outline for the
vendor's proposal response (an ordered list of section headings the proposal must follow).

Output ONLY a single JSON object with these exact keys, no prose, no markdown code fences, no
commentary before or after:
{{
  "structure_specified": true or false,
  "sections": ["First required section heading", "Second required section heading", "..."],
  "reasoning": "one or two sentences explaining the verdict — cite where in the document the
    structure requirement appears, or note that none was found"
}}

If structure_specified is false, return an empty list for "sections". Use the RFP's own wording
for each heading (don't paraphrase or rename them), in the order the RFP lists them."""

    return SYSTEM_PROMPT, user_prompt
