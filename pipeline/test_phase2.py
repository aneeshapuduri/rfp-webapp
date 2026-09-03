#!/usr/bin/env python3
"""
Tests the complete Phase 2 loop against the Northfield (ambiguous) case:
  1. Confirm the gate halts (from Phase 1 result)
  2. Generate the client-facing Clarification Questions .docx + sidecar mapping
  3. Simulate the client filling in the Answer column of the actual generated .docx
     (not a fabricated dict — this proves the real document round-trips)
  4. Read the filled document back via response_reader.py
  5. Resolve each answer (canned resolutions standing in for the live Claude call)
  6. Re-check the gate — expect it to now proceed: a response that came back insufficient is
     escalated_for_manual_review (carried into Phase 3/4 as an assumption-needing item) rather
     than blocking the pipeline forever waiting for a client answer that will never arrive
"""
import pathlib
import sys

import docx

from demo_data import NORTHFIELD_ITEMS
from phase1_pipeline import guess_agency, guess_project_title
from phase2_pipeline import check_gate, generate_clarification_package, resolve_with_responses
from response_reader import read_client_responses
from rfp_reader import read_rfp
from schema import Phase1Result

SAMPLE_DIR = pathlib.Path(__file__).resolve().parent.parent / "sample_data"
OUTPUT_DIR = pathlib.Path(__file__).resolve().parent.parent / "outputs"
OUTPUT_DIR.mkdir(exist_ok=True)

# Mock client answers, keyed by question number as they'll appear in the generated table.
# Q5 (hosting standards) is deliberately non-committal to test the escalation branch.
MOCK_ANSWERS = {
    "1": "We want a full replacement, not an in-place upgrade. Target 99.9% uptime; current downtime averages about 6 hours per month.",
    "2": "Integration is needed with our Motorola APX radio system via its P25 CAD interface, and with our Mark43 CAD platform.",
    "3": "Current volume is approximately 450 calls per day, with projected growth of 15% over the next 3 years.",
    "4": "The system must comply with the CJIS Security Policy given the nature of the data.",
    "5": "We don't have documented standards for this — use your best judgement.",
    "6": "Approximately 200,000 historical records dating back 10 years, currently stored in a SQL Server database.",
    "7": "We need the project completed within 6 months of contract award.",
}

# Canned resolutions standing in for a live Claude call to build_resolution_prompt's output.
# Six answers give a solutions architect enough to design against; #5 does not (vague,
# non-committal "use your best judgement") and should be escalated rather than accepted.
DEMO_RESOLUTIONS = {
    "REQ-001": {"resolved": True, "updated_requirement": "Full replacement of dispatch software, targeting 99.9% uptime (current baseline: ~6 hours/month downtime)", "reasoning": "Replacement vs. upgrade and a concrete uptime target were both given."},
    "REQ-002": {"resolved": True, "updated_requirement": "Integrate with Motorola APX radios (P25 CAD interface) and Mark43 CAD platform", "reasoning": "Specific systems and interface named."},
    "REQ-003": {"resolved": True, "updated_requirement": "Support ~450 calls/day current volume with 15% growth over 3 years", "reasoning": "Concrete current volume and growth rate given."},
    "REQ-004": {"resolved": True, "updated_requirement": "Security architecture compliant with CJIS Security Policy", "reasoning": "Named a specific, well-defined compliance standard."},
    "REQ-005": {"resolved": False, "updated_requirement": None, "reasoning": "Answer explicitly defers the decision back to the vendor without naming any actual constraint, standard, or hosting preference — still no basis for a compliant architecture decision."},
    "REQ-006": {"resolved": True, "updated_requirement": "Migrate ~200,000 historical dispatch records (10-year range) from SQL Server", "reasoning": "Volume, date range, and source system all given."},
    "REQ-008": {"resolved": True, "updated_requirement": "Complete the project within 6 months of contract award", "reasoning": "Concrete deadline given."},
}


def main():
    print("=" * 70)
    print("PHASE 2 FULL LOOP TEST — Northfield (ambiguous) case")
    print("=" * 70)

    rfp_text = read_rfp(str(SAMPLE_DIR / "sample_rfp_ambiguous.txt"))
    project_title = guess_project_title(rfp_text)
    agency = guess_agency(rfp_text)
    result = Phase1Result.from_raw(project_title, agency, NORTHFIELD_ITEMS)

    # Step 1: gate check
    gate = check_gate(result)
    print(f"\n[1] Gate check: {gate}")
    assert gate == "halt_for_clarification", "Expected the gate to halt on the ambiguous case"

    # Step 2: generate clarification doc + mapping
    doc_path = str(OUTPUT_DIR / "clarification_questions_northfield.docx")
    mapping = generate_clarification_package(result, "Meridian Systems Group", doc_path)
    print(f"[2] Generated: {doc_path}")
    print(f"    Mapping (Q# -> requirement id): {mapping}")
    assert len(mapping) == len(result.get_ambiguous()), "Mapping should cover every ambiguous item"

    # Step 3: simulate the client filling in the actual generated .docx
    d = docx.Document(doc_path)
    table = d.tables[0]
    filled_count = 0
    for row in table.rows[1:]:
        q_num = row.cells[0].text.strip()
        if q_num in MOCK_ANSWERS:
            row.cells[2].text = MOCK_ANSWERS[q_num]
            filled_count += 1
    filled_path = str(OUTPUT_DIR / "clarification_questions_northfield_FILLED.docx")
    d.save(filled_path)
    print(f"[3] Simulated client filled in {filled_count} answers -> {filled_path}")

    # Step 4: read the filled document back
    mapping_path = str(pathlib.Path(doc_path).with_suffix(".mapping.json"))
    responses = read_client_responses(filled_path, mapping_path)
    print(f"[4] Read back {len(responses)} responses, matched to requirement IDs:")
    for req_id, ans in responses.items():
        print(f"    {req_id}: {ans[:70]}...")
    assert len(responses) == len(MOCK_ANSWERS), "Should have read back every answered question"

    # Step 5 & 6: resolve and re-check the gate
    result = resolve_with_responses(result, responses, client=None, demo_resolutions=DEMO_RESOLUTIONS)
    gate_after = check_gate(result)
    print(f"\n[5/6] Gate after resolving responses: {gate_after}")
    print(f"      Summary: {result.summary}")
    escalated = result.get_escalated()
    print(f"      Escalated for manual review: {[r.id for r in escalated]}")

    assert gate_after == "proceed", (
        "Expected the gate to proceed: REQ-005 is escalated_for_manual_review, not still "
        "blocking — the pipeline must not get stuck forever on a response that already came "
        "back insufficient once"
    )
    assert len(escalated) == 1 and escalated[0].id == "REQ-005", (
        "Expected exactly REQ-005 to be escalated for manual review"
    )
    print("\n      -> Correctly distinguishes 'needs a human's judgment call, downstream' from")
    print("         'needs another auto-question before we can proceed at all':")
    print("         6 of 7 requirements auto-resolved and are now 'clear'.")
    print("         1 requirement (REQ-005) has a response on file but it didn't actually answer")
    print("         the question — flagged for manual review and carried forward as an")
    print("         assumption rather than blocking the pipeline indefinitely.")

    # Sanity: confirm the resolved items really did update, not just flip status blindly
    req1 = result.by_id("REQ-001")
    print(f"\n      REQ-001 updated requirement text: {req1.requirement}")
    assert "replacement" in req1.requirement.lower()

    out_path = OUTPUT_DIR / "phase2_resolved_northfield.json"
    out_path.write_text(__import__("json").dumps(result.to_dict(), indent=2), encoding="utf-8")
    print(f"\nSaved final resolved state -> {out_path}")

    print("\n" + "=" * 70)
    print("ALL PHASE 2 TESTS PASSED")
    print("=" * 70)


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print(f"\nTEST FAILED: {e}", file=sys.stderr)
        sys.exit(1)
