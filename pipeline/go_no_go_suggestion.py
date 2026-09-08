"""
Fresh, LLM-based Go/No-Go recommendation computed once a project reaches the Summary tab (see
main.py's project_detail route) — see go_no_go_suggestion_prompts.py's module docstring for how
this differs from the early, deterministic capability-fit check in pipeline/go_no_go.py.

Computed synchronously from an interactive route (main.py's accept_assumptions and the new
preview/resubmit route) rather than as a background task: unlike Phase 1-4, this is a single,
fast classification call (no drafting), and computing it inline means the Summary tab has a
suggestion the moment the page loads rather than needing the user to poll or refresh. Like every
other AI-assist check in this pipeline, it's non-fatal — pipeline_runner.py's
_run_phase3_and_4/main.py's callers swallow and log any failure so a bad or unavailable LLM call
never blocks the underlying status transition.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from claude_client import ClaudeClient
from go_no_go_suggestion_prompts import build_go_no_go_suggestion_prompt

_VALID_OVERALL = {"Go", "Go, with gaps", "No-Go"}


@dataclass
class GoNoGoSuggestion:
    overall: str
    reasoning: str
    key_considerations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "overall": self.overall,
            "reasoning": self.reasoning,
            "key_considerations": self.key_considerations,
        }


def compute_go_no_go_suggestion(
    project_title: str,
    agency: str,
    capability_fit: dict | None,
    compliance_matrix: list[dict] | None,
    rfp_summary: dict | None,
    assumptions: list[str] | None,
    client: ClaudeClient,
) -> GoNoGoSuggestion:
    system_prompt, user_prompt = build_go_no_go_suggestion_prompt(
        project_title, agency, capability_fit, compliance_matrix, rfp_summary, assumptions,
    )
    raw = client.generate_json(system_prompt, user_prompt, max_tokens=800)

    if not isinstance(raw, dict):
        raise RuntimeError(
            f"Expected a JSON object for the Go/No-Go suggestion, got {type(raw)}: {raw}"
        )

    overall = str(raw.get("overall") or "").strip()
    if overall not in _VALID_OVERALL:
        overall = "Go, with gaps"  # a safe, human-review-prompting default if the model drifts

    reasoning = str(raw.get("reasoning") or "").strip() or "No reasoning was returned."
    considerations = raw.get("key_considerations") or []
    if not isinstance(considerations, list):
        considerations = []

    return GoNoGoSuggestion(
        overall=overall,
        reasoning=reasoning,
        key_considerations=[str(c).strip() for c in considerations if str(c).strip()],
    )
