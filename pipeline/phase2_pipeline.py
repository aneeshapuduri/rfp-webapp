"""
Phase 2: Decision Gate & Clarification Flow.

Three entry points used by the web app (Phase 5):
  - check_gate(result): does this project halt for clarification, or proceed to Phase 3?
  - generate_clarification_package(result, ...): produce the client-facing doc + mapping
  - resolve_with_responses(result, responses, client): ingest client answers, update the
    result, and re-check the gate — this is the "resume after client responds" flow.
"""
from __future__ import annotations

from claude_client import ClaudeClient
from clarification_doc_builder import build_clarification_doc
from resolution_prompts import build_resolution_prompt
from schema import Phase1Result


def check_gate(result: Phase1Result) -> str:
    """Returns 'proceed' or 'halt_for_clarification'."""
    return result.pipeline_decision


def generate_clarification_package(result: Phase1Result, company_name: str, output_path: str) -> dict:
    """Generates the .docx + sidecar mapping. Returns the mapping dict."""
    return build_clarification_doc(result, company_name, output_path)


def resolve_with_responses(
    result: Phase1Result,
    responses: dict[str, str],
    client: ClaudeClient | None,
    demo_resolutions: dict[str, dict] | None = None,
) -> Phase1Result:
    """
    Applies client responses to the still-ambiguous requirements in `result`, in place, and
    returns it. Each response is evaluated independently:
      - resolved=true  -> status becomes 'clear', requirement text updated, response stored
      - resolved=false -> status stays 'ambiguous' but escalated_for_manual_review=True,
        so it stops blocking the automated pipeline via another auto-question loop, while
        still being visibly flagged rather than silently treated as clear.

    demo_resolutions lets tests/demo mode supply canned {requirement_id: {"resolved":.., ...}}
    results instead of calling the live API, exercising the exact same merge logic below.
    """
    for req_id, answer in responses.items():
        item = result.by_id(req_id)
        if item is None or item.status != "ambiguous":
            continue  # ignore responses for requirements that aren't currently ambiguous

        item.client_response = answer

        if demo_resolutions is not None:
            resolution = demo_resolutions.get(req_id)
            if resolution is None:
                continue
        else:
            if client is None:
                raise RuntimeError("No Claude client provided and no demo_resolutions supplied.")
            sys_p, user_p = build_resolution_prompt(
                item.requirement, item.clarification_question or "", answer
            )
            resolution = client.generate_json(sys_p, user_p, max_tokens=500)

        if resolution.get("resolved"):
            item.status = "clear"
            item.requirement = resolution.get("updated_requirement") or item.requirement
            item.reasoning = resolution.get("reasoning", item.reasoning)
            item.escalated_for_manual_review = False
        else:
            item.escalated_for_manual_review = True
            item.reasoning = (
                item.reasoning + " | Client response received but insufficient: "
                + resolution.get("reasoning", "")
            )

    return result
