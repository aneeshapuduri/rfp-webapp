"""
Prompt for detecting RFP-specific submission requirements our fixed 31-section template
(template_mapper.OUR_SECTIONS) doesn't already cover — e.g. an RFP that asks bidders to attach
a signed Certificate of Insurance, resumes for named key staff, three client references, a
Disaster Recovery Plan, a signed addendum acknowledgment form, and so on. These are almost
always explicit "include/attach/provide" instructions rather than ordinary scope requirements,
so the prompt is deliberately conservative: it only asks for items that call for their OWN
distinct section/exhibit, not anything already addressed by one of the 31 canonical sections
(including the three assumptions-related ones, Compliance Matrix, or Effort & Pricing Summary).
"""

BASE_SYSTEM = """You are a proposal compliance analyst reviewing a finalized RFP requirement
list to check for anything a standard proposal template would miss. Be conservative — most
requirements are already covered by a standard proposal's narrative sections. Never invent a
requirement that isn't in the list given."""


def extra_sections_prompt(requirements: list[str], our_sections: list[str]) -> tuple[str, str]:
    reqs = "\n".join(f"- {r}" for r in requirements)
    sections = "\n".join(f"- {s}" for s in our_sections)
    user = f"""FINALIZED REQUIREMENTS EXTRACTED FROM THE RFP:
{reqs}

OUR PROPOSAL TEMPLATE ALREADY INCLUDES THESE {len(our_sections)} SECTIONS:
{sections}

Some RFPs explicitly ask the bidder to attach or include a distinct document/exhibit/section
that a standard proposal template wouldn't already cover — for example a signed Certificate of
Insurance, resumes or references for named key staff, a Disaster Recovery/Business Continuity
Plan, a subcontractor disclosure form, a diversity/MWBE certification, a signed addendum
acknowledgment, or a specific attachment the RFP names by number or letter.

Review the requirements above and identify ONLY requirements of that kind — an explicit
instruction to include a specific extra document/section in the proposal submission that
none of the {len(our_sections)} template sections above would reasonably contain. Do NOT
include anything already covered by an existing section (do not flag pricing, staffing,
assumptions, scope, timeline, or general compliance-matrix items — those are all handled by
sections already in the list above).

For each one you find, draft a short, professional section addressing it directly, grounded
only in what the requirement actually asks for — do not invent specifics (names, policy
numbers, dates) that were not given.

Output ONLY a JSON array (no prose, no markdown fences). Each element:
{{"title": "short section title (3-6 words, title case)", "content": "the drafted section
body, 60-150 words, plain prose or \\"- \\" bullet lines, no heading"}}

If nothing qualifies, output exactly: []"""
    return BASE_SYSTEM, user
