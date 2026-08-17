# Ambiguity Detection Criteria (SBA Role)

Used by the Phase 1 requirement-extraction engine to decide, for each extracted requirement,
whether it is **clear enough to proceed** or **ambiguous enough to require a clarification
question** before the SA role can responsibly propose a solution, staffing plan, or price.

## A requirement is flagged AMBIGUOUS if any of the following are true:

1. **Missing implementation detail** — the requirement states an outcome without enough
   detail to size the work (e.g. "integrate with the City's work-order system" without
   naming the system, its API/interface type, or data format).
2. **Undefined infrastructure/networking constraints** — hosting environment, network
   architecture, connectivity requirements, or on-prem vs. cloud expectations are not
   stated where they materially affect solution design or cost.
3. **Unscoped volume/scale** — the RFP implies a system with real throughput/capacity needs
   (users, transactions, records, devices) but gives no number, or gives a number with no
   growth/peak expectation where that matters to sizing.
4. **Conflicting statements** — two sections of the RFP describe the same requirement
   differently (e.g. differing deadlines, differing scope boundaries).
5. **Undefined compliance/security bar** — the RFP references compliance in general terms
   ("must be secure," "must protect data") without naming a standard, or names a standard
   without specifying impact level/scope (e.g. "NIST 800-53" without stating baseline).
6. **Unstated ownership/dependency** — a requirement depends on an external system, dataset,
   or decision the client controls, and the RFP doesn't say whether/when that dependency will
   be made available.
7. **Undefined acceptance criteria** — a deliverable is named without any measurable
   definition of "done" (e.g. "improve performance" with no baseline or target metric).

## A requirement is NOT flagged (treated as clear) if:

- It is specific enough that a reasonable solution architect could size and design against it
  using standard industry practice, even if not every last detail is spelled out.
- Minor stylistic/formatting requirements (e.g. font, page limits) — these don't block solution
  design and should just be followed as stated, not flagged.
- The RFP explicitly states the vendor should propose their own approach/technology
  (this is normal — it becomes part of the SA's recommendation, not a gap).

## Confidence handling

Each extracted requirement gets a status: `clear`, `ambiguous`, or `assumption_needed`.

- `ambiguous` → included in the Clarification Questions document; pipeline halts.
- `assumption_needed` → borderline cases where a reasonable, clearly-labeled assumption is
  safer than a client round-trip (e.g. "assume standard business-hours support unless
  otherwise specified"). These do NOT halt the pipeline — they get logged as explicit
  **Assumptions** and carried into the final proposal's Assumptions section, so the client
  sees exactly what was assumed and can correct it post-submission if needed.
- `clear` → proceeds normally.

## Output format per requirement

```json
{
  "requirement": "short restatement",
  "source_section": "where in the RFP this came from",
  "status": "clear | ambiguous | assumption_needed",
  "reasoning": "why this status was assigned",
  "clarification_question": "only present if status is ambiguous",
  "assumption_text": "only present if status is assumption_needed"
}
```

## Threshold for halting the pipeline

If **one or more** requirements are `ambiguous`, the pipeline halts after Phase 1/2 and
produces the Clarification Questions document. `assumption_needed` items alone never halt
the pipeline — only genuine `ambiguous` items do, since assumptions are recoverable
post-submission but a wrong ambiguous guess (e.g. wrong compliance bar) can disqualify a bid.
