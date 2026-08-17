#!/usr/bin/env python3
"""
Tests Phase 4:
  1. Compliance completeness check catches a deliberately incomplete matrix (missing REQ-008)
  2. Full pipeline assembles a complete, valid final proposal .docx
  3. Template mapper correctly matches/flags headings from a mock client template
"""
import pathlib
import sys

from demo_data import LAKEVIEW_ITEMS
from demo_data_phase3 import LAKEVIEW_STAFFING_PLAN, LAKEVIEW_TECH_APPROACH
from document_builder import build_final_proposal
from narrative_demo_data import BROKEN_COMPLIANCE_MATRIX, COMPLIANCE_MATRIX, DEMO_NARRATIVE
from phase1_pipeline import guess_agency, guess_project_title
from phase3_pipeline import run_phase3
from phase4_pipeline import ComplianceMatrixIncompleteError, run_phase4
from rfp_reader import read_rfp
from schema import Phase1Result
from template_mapper import map_sections_to_template

SAMPLE_DIR = pathlib.Path(__file__).resolve().parent.parent / "sample_data"
OUTPUT_DIR = pathlib.Path(__file__).resolve().parent.parent / "outputs"
OUTPUT_DIR.mkdir(exist_ok=True)


def build_lakeview_result():
    rfp_text = read_rfp(str(SAMPLE_DIR / "sample_rfp.txt"))
    return Phase1Result.from_raw(guess_project_title(rfp_text), guess_agency(rfp_text), LAKEVIEW_ITEMS)


def part1_completeness_check():
    print("=" * 70)
    print("PART 1: Compliance matrix completeness check")
    print("=" * 70)
    result = build_lakeview_result()
    phase3 = run_phase3(result, 9, demo_technology_approach=LAKEVIEW_TECH_APPROACH, demo_staffing_plan=LAKEVIEW_STAFFING_PLAN)

    broken_narrative = dict(DEMO_NARRATIVE)
    broken_narrative["compliance_matrix"] = BROKEN_COMPLIANCE_MATRIX

    try:
        run_phase4(result, phase3, 9, demo_narrative=broken_narrative)
        print("FAILED — should have raised ComplianceMatrixIncompleteError for missing REQ-008")
        sys.exit(1)
    except ComplianceMatrixIncompleteError as e:
        print(f"Correctly caught incomplete matrix:\n{e}")
        assert "REQ-008" in str(e)
        print("\nPASS — an incomplete compliance matrix cannot silently reach a client document.")


def part2_full_assembly():
    print("\n" + "=" * 70)
    print("PART 2: Full document assembly")
    print("=" * 70)
    result = build_lakeview_result()
    phase3 = run_phase3(result, 9, demo_technology_approach=LAKEVIEW_TECH_APPROACH, demo_staffing_plan=LAKEVIEW_STAFFING_PLAN)
    content = run_phase4(result, phase3, 9, demo_narrative=DEMO_NARRATIVE)

    assert len(content["compliance_matrix"]) == 10, "Expected all 10 Lakeview requirements in the matrix"
    print(f"Compliance matrix covers all {len(content['compliance_matrix'])} requirements.")

    out_path = str(OUTPUT_DIR / "final_proposal_lakeview.docx")
    build_final_proposal(out_path, content)
    print(f"Built final proposal -> {out_path}")
    assert pathlib.Path(out_path).exists()


def part3_template_mapping():
    print("\n" + "=" * 70)
    print("PART 3: Client template heading mapping (v1-limited)")
    print("=" * 70)
    # Mock a client template with some headings that should match and one that's unrecognizable.
    mock_headings = [
        "1.0 Executive Overview",
        "2.0 Our Understanding of Your Needs",
        "3.0 Technical Solution",
        "4.0 Project Staffing",
        "5.0 Schedule",
        "6.0 Cost Proposal",
        "7.0 References",
        "8.0 Requirements Traceability Matrix",
        "9.0 Appendix Z: Vendor History",  # should NOT match anything well
    ]
    result = map_sections_to_template(mock_headings)
    print("Matched:")
    for our_section, heading in result["matched"].items():
        print(f"  {our_section!r:45} -> {heading!r}")
    print("Unmatched (need manual placement):")
    for s in result["unmatched"]:
        print(f"  {s!r}")

    assert "Executive Summary" in result["matched"], "Should have matched Executive Overview"
    assert len(result["unmatched"]) > 0, "Expected at least Assumptions or similar to need manual placement"
    print("\nPASS — confident matches are used, low-confidence ones are flagged rather than guessed.")


if __name__ == "__main__":
    try:
        part1_completeness_check()
        part2_full_assembly()
        part3_template_mapping()
        print("\n" + "=" * 70)
        print("ALL PHASE 4 TESTS PASSED")
        print("=" * 70)
    except AssertionError as e:
        print(f"\nTEST FAILED: {e}", file=sys.stderr)
        sys.exit(1)
