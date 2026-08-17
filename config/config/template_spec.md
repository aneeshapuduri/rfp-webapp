# Default Proposal Template Spec

Based on standard structure expected across most government and enterprise RFPs (the
sections evaluators typically score against), plus the additions this workflow requires
(assumptions, staffing/effort, pricing). If a client-provided template is uploaded later, the
assembly engine maps generated content into that template's headings instead of this default.

## Section order

1. **Cover Page** — project title, issuing agency name, company name/logo placeholder, date
2. **Table of Contents**
3. **Executive Summary** — understanding + solution + why us, high-level
4. **Understanding of the Problem/Requirements** — demonstrates comprehension, no solutioning
5. **Technical Approach & Solution Architecture** — SA's proposed technology/approach, organized
   by requirement area; explicitly separates *stated requirements* from *proposed approach*
6. **Assumptions** — every `assumption_needed` item from Phase 1, stated plainly so the client
   can correct any of them
7. **Proposed Team & Staffing Plan** — roles required, headcount per role, key personnel bios
   (from company profile) mapped to roles
8. **Project Timeline / Schedule** — phased delivery plan with durations
9. **Effort & Pricing Summary** — role × hours × rate table, contingency line, total; clearly
   labeled as a competitive market-rate estimate for internal SA review before submission
10. **Relevant Past Performance** — company profile past performance examples, each tied back
    to this RFP's requirements
11. **Compliance Matrix** — every extracted requirement, our response, and compliance status
    (this is the most scrutinized section — must cover 100% of extracted requirements, no gaps)
12. **Closing Statement** — contact info, invitation for questions

## Formatting standards

- Cover page + section headings use company brand colors if provided, else a neutral
  professional navy/gray palette (as used in the earlier prototype)
- All tables (timeline, pricing, compliance matrix) use consistent column widths and a
  shaded header row
- Page numbers and a running footer with project name + "Confidential — Proposal" on every
  page after the cover
- Word (.docx) as the default output format, since that's the near-universal RFP submission
  format; PDF export offered as a secondary option

## Client-provided template handling (future capability, not required for v1)

When a client uploads their own template: parse its heading structure, map each of the 12
sections above to the closest matching heading (fuzzy match on heading text), insert
generated content under matching headings, and flag any of the 12 sections that couldn't be
confidently matched for manual placement rather than guessing wrong.
