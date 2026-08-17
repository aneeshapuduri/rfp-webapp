#!/usr/bin/env python3
"""
Tests Phase 3 in two parts:
  1. Happy path — Lakeview staffing plan through the real pricing engine, verify the math
     is exactly right by hand-checking one line item, and confirm sanity checks pass clean.
  2. Failure path — a deliberately understaffed plan, proving the sanity checker actually
     catches it rather than silently producing a document with an implausible price.
"""
import json
import pathlib
import sys

from demo_data import LAKEVIEW_ITEMS
from demo_data_phase3 import BROKEN_STAFFING_PLAN, LAKEVIEW_STAFFING_PLAN, LAKEVIEW_TECH_APPROACH
from phase1_pipeline import guess_agency, guess_project_title
from phase3_pipeline import run_phase3
from pricing_engine import build_pricing_summary, load_rate_card
from rfp_reader import read_rfp
from sanity_checks import has_blocking_errors, run_sanity_checks
from schema import Phase1Result

SAMPLE_DIR = pathlib.Path(__file__).resolve().parent.parent / "sample_data"
OUTPUT_DIR = pathlib.Path(__file__).resolve().parent.parent / "outputs"
OUTPUT_DIR.mkdir(exist_ok=True)


def part1_happy_path():
    print("=" * 70)
    print("PART 1: Lakeview happy path — pricing math + clean sanity check")
    print("=" * 70)

    rfp_text = read_rfp(str(SAMPLE_DIR / "sample_rfp.txt"))
    result = Phase1Result.from_raw(
        guess_project_title(rfp_text), guess_agency(rfp_text), LAKEVIEW_ITEMS
    )
    assert result.pipeline_decision == "proceed", "Lakeview should be clear to proceed"

    phase3 = run_phase3(
        result,
        duration_months=9,
        demo_technology_approach=LAKEVIEW_TECH_APPROACH,
        demo_staffing_plan=LAKEVIEW_STAFFING_PLAN,
    )

    pricing = phase3.pricing
    print(f"\nTotal hours: {pricing['total_hours']}")
    print(f"Labor subtotal: ${pricing['labor_subtotal']:,.2f}")
    print(f"Contingency ({pricing['contingency_pct']}%): ${pricing['contingency_amount']:,.2f}")
    print(f"TOTAL: ${pricing['total']:,.2f}")

    # Hand-verify one line item's math independently of the engine.
    rate_card = load_rate_card()
    pm_rate = next(r["blended_hourly_rate"] for r in rate_card["roles"] if r["role"] == "Program Manager")
    expected_pm_subtotal = 1 * 720 * pm_rate
    pm_line = next(l for l in pricing["lines"] if l["role"] == "Program Manager")
    print(f"\nHand-check Program Manager: 1 x 720 hrs x ${pm_rate}/hr = ${expected_pm_subtotal:,.2f}")
    print(f"Engine computed: ${pm_line['subtotal']:,.2f}")
    assert pm_line["subtotal"] == round(expected_pm_subtotal, 2), "Pricing engine math mismatch!"
    print("MATCH — pricing engine is computing correctly, not approximating.")

    # Hand-verify the grand total independently.
    expected_labor = sum(
        item["headcount"] * item["hours_per_person"] *
        next(r["blended_hourly_rate"] for r in rate_card["roles"] if r["role"] == item["role"])
        for item in LAKEVIEW_STAFFING_PLAN
    )
    expected_contingency = expected_labor * (rate_card["pricing_rules"]["contingency_buffer_pct"] / 100)
    expected_total = round(expected_labor + expected_contingency, 2)
    print(f"\nHand-check grand total: ${expected_total:,.2f}")
    print(f"Engine computed total:  ${pricing['total']:,.2f}")
    assert abs(pricing["total"] - expected_total) < 0.01, "Grand total mismatch!"
    print("MATCH.")

    print(f"\nSanity flags: {phase3.sanity_flags}")
    assert phase3.sanity_flags == [], "Expected a clean sanity check on a realistic staffing plan"
    print("No sanity flags raised — staffing plan looks realistic for a 9-month project.")

    out_path = OUTPUT_DIR / "phase3_lakeview.json"
    out_path.write_text(json.dumps(phase3.to_dict(), indent=2), encoding="utf-8")
    print(f"\nSaved -> {out_path}")


def part2_failure_path():
    print("\n" + "=" * 70)
    print("PART 2: Deliberately broken staffing plan — proving the sanity check fires")
    print("=" * 70)

    rfp_text = read_rfp(str(SAMPLE_DIR / "sample_rfp.txt"))
    result = Phase1Result.from_raw(
        guess_project_title(rfp_text), guess_agency(rfp_text), LAKEVIEW_ITEMS
    )

    phase3 = run_phase3(
        result,
        duration_months=9,
        demo_technology_approach=LAKEVIEW_TECH_APPROACH,
        demo_staffing_plan=BROKEN_STAFFING_PLAN,
    )

    print(f"\nTotal hours: {phase3.pricing['total_hours']} across 9 months")
    print(f"Grand total: ${phase3.pricing['total']:,.2f}")
    print("\nSanity flags raised:")
    for f in phase3.sanity_flags:
        print(f"  [{f['severity'].upper()}] {f['message']}")

    assert len(phase3.sanity_flags) > 0, "Expected the understaffed plan to trigger sanity flags"
    assert any(f["severity"] == "error" for f in phase3.sanity_flags), "Expected at least one blocking error"
    print("\nCorrectly flagged as blocking — this would NOT be allowed to silently reach a client document.")

    # Also confirm an unknown role is rejected rather than silently priced at $0 or guessed.
    print("\nAlso testing: role not in rate card catalog...")
    try:
        build_pricing_summary([{"role": "Chief Wizard", "headcount": 1, "hours_per_person": 100}])
        print("FAILED — should have raised an error for an unknown role")
        sys.exit(1)
    except ValueError as e:
        print(f"Correctly rejected: {e}")


if __name__ == "__main__":
    try:
        part1_happy_path()
        part2_failure_path()
        print("\n" + "=" * 70)
        print("ALL PHASE 3 TESTS PASSED")
        print("=" * 70)
    except AssertionError as e:
        print(f"\nTEST FAILED: {e}", file=sys.stderr)
        sys.exit(1)
