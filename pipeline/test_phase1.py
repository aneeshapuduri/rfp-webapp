#!/usr/bin/env python3
"""
Tests the Phase 1 schema, validation, and halt/proceed decision logic against both sample
RFPs using hand-authored classifications (demo_data.py) — the same code path real Claude
output will flow through, just with a stand-in for the API call itself.

Usage: python test_phase1.py
"""
import json
import pathlib
import sys

from demo_data import LAKEVIEW_ITEMS, NORTHFIELD_ITEMS
from phase1_pipeline import guess_agency, guess_project_title
from rfp_reader import read_rfp
from schema import Phase1Result

SAMPLE_DIR = pathlib.Path(__file__).resolve().parent.parent / "sample_data"
OUTPUT_DIR = pathlib.Path(__file__).resolve().parent.parent / "outputs"


def run_case(label: str, rfp_filename: str, raw_items: list[dict], expected_decision: str):
    print(f"\n{'=' * 70}\nCASE: {label}\n{'=' * 70}")
    rfp_path = SAMPLE_DIR / rfp_filename
    rfp_text = read_rfp(str(rfp_path))
    project_title = guess_project_title(rfp_text)
    agency = guess_agency(rfp_text)

    result = Phase1Result.from_raw(project_title, agency, raw_items)

    errors = result.validate()
    if errors:
        print("VALIDATION FAILED:")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)
    print("Validation: PASS (schema valid, no missing required fields)")

    print(f"Project: {result.project_title}")
    print(f"Agency:  {result.agency}")
    print(f"Summary: {result.summary}")
    print(f"Decision: {result.pipeline_decision}")

    if result.pipeline_decision != expected_decision:
        print(f"UNEXPECTED DECISION — expected '{expected_decision}', got '{result.pipeline_decision}'")
        sys.exit(1)
    print(f"Decision matches expected: {expected_decision}  ✓")

    print("\nRequirement-by-requirement:")
    for r in result.requirements:
        tag = {"clear": "CLEAR", "ambiguous": "AMBIGUOUS", "assumption_needed": "ASSUMPTION"}[r.status]
        print(f"  [{tag:10}] {r.id}: {r.requirement[:80]}")
        if r.status == "ambiguous":
            print(f"               -> Q: {r.clarification_question}")
        if r.status == "assumption_needed":
            print(f"               -> Assumption: {r.assumption_text}")

    OUTPUT_DIR.mkdir(exist_ok=True)
    out_path = OUTPUT_DIR / f"phase1_{label.lower().replace(' ', '_')}.json"
    out_path.write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")
    print(f"\nSaved structured output -> {out_path}")
    return result


if __name__ == "__main__":
    run_case("clear_case_lakeview", "sample_rfp.txt", LAKEVIEW_ITEMS, expected_decision="proceed")
    run_case("ambiguous_case_northfield", "sample_rfp_ambiguous.txt", NORTHFIELD_ITEMS, expected_decision="halt_for_clarification")
    print(f"\n{'=' * 70}\nALL TESTS PASSED\n{'=' * 70}")
