#!/usr/bin/env python3
"""
Production entry point for Phase 1: Intake & Requirement Extraction.

Usage:
    python run_phase1.py --rfp path/to/bid_invitation.pdf --out outputs/phase1_result.json

Requires ANTHROPIC_API_KEY to be set in the environment.
"""
import argparse
import json
import sys

from claude_client import ClaudeClient
from phase1_pipeline import run_phase1
from rfp_reader import read_rfp


def main():
    parser = argparse.ArgumentParser(description="Run Phase 1 (SBA requirement extraction) on an RFP.")
    parser.add_argument("--rfp", required=True, help="Path to the bid invitation (.txt, .docx, .pdf)")
    parser.add_argument("--out", default="outputs/phase1_result.json", help="Output JSON path")
    args = parser.parse_args()

    try:
        client = ClaudeClient()
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    rfp_text = read_rfp(args.rfp)
    print("Extracting and classifying requirements...")
    result = run_phase1(rfp_text, client)

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(result.to_dict(), f, indent=2)

    print(f"\nProject: {result.project_title}")
    print(f"Agency:  {result.agency}")
    print(f"Summary: {result.summary}")
    print(f"Decision: {result.pipeline_decision}")
    print(f"\nSaved -> {args.out}")

    if result.pipeline_decision == "halt_for_clarification":
        print("\n>> Pipeline will halt here — ambiguous requirements need client clarification "
              "before Phase 3 (Solution Architecture) can proceed. This is handled in Phase 2.")


if __name__ == "__main__":
    main()
