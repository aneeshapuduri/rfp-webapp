"""
Generates the small set of supporting visuals embedded in the final proposal .docx: a Gantt-style
timeline chart, a staffing/effort bar chart, and a left-to-right delivery-approach flow diagram.
All three are built from data the pipeline already has (the Phase 4 timeline and the Phase 3
staffing/pricing lines) — nothing here calls an LLM or invents numbers, it only visualizes what
document_builder.py would otherwise render as a plain table.

Every builder function returns PNG bytes (or None if there's nothing sensible to draw, e.g. an
empty timeline) and never raises — a chart that can't be drawn from unusual data should mean no
picture in that section, not a failed proposal. document_builder.py is responsible for skipping
the image gracefully when a builder returns None.
"""
from __future__ import annotations

import io
import re

import matplotlib

matplotlib.use("Agg")  # headless — no display available on a server
import matplotlib.pyplot as plt

NAVY = "#1F2D50"
ACCENT = "#2E74B5"
LIGHT_GRID = "#D9D9D9"

_NUMBERS_RE = re.compile(r"\d+(?:\.\d+)?")

# Phase durations aren't guaranteed to share a unit — an LLM-generated timeline (see
# narrative_prompts.timeline_prompt) will happily write "Weeks 1-4" for near-term phases and
# "Months 9-21" for a long post-launch support tail in the very same list. Plotting the raw
# numbers as-is in that case silently overlaps unrelated phases (a "9-21" meant as months lands
# on top of single-digit week numbers). Everything is normalized to weeks before plotting so
# spans stay on one consistent scale regardless of which unit each phase happened to use.
_UNIT_TO_WEEKS = {
    "quarter": 13.0, "quarters": 13.0,
    "month": 4.345, "months": 4.345,
    "week": 1.0, "weeks": 1.0,
    "day": 1.0 / 7, "days": 1.0 / 7,
}


def _unit_multiplier(duration: str) -> float:
    text = (duration or "").lower()
    for unit, multiplier in _UNIT_TO_WEEKS.items():
        if unit in text:
            return multiplier
    return 1.0  # no recognizable unit keyword — assume weeks, the common case


def _parse_duration_span(duration: str, fallback_start: float, fallback_span: float = 1.0) -> tuple[float, float]:
    """Best-effort parse of a free-text duration like 'Weeks 1-4', 'Week 5', or 'Months 9-21'
    into a (start, end) span in weeks for plotting. Phase durations come from an LLM-generated
    timeline, so the exact wording (and unit) varies — this never raises; anything it can't
    confidently parse falls back to a sequential slot so the chart still renders with every
    phase in order, just without a precisely-to-scale span for that one bar."""
    numbers = [float(n) for n in _NUMBERS_RE.findall(duration or "")]
    multiplier = _unit_multiplier(duration)
    if len(numbers) >= 2:
        start, end = numbers[0] * multiplier, numbers[1] * multiplier
        if end > start:
            return start - multiplier, end
    if len(numbers) == 1:
        value = numbers[0] * multiplier
        return value - multiplier, value
    return fallback_start, fallback_start + fallback_span


def build_timeline_chart(timeline: list[dict]) -> bytes | None:
    """Horizontal Gantt-style bar chart, one bar per project phase, spanning its parsed
    duration. Returns None if there are no phases to plot."""
    if not timeline:
        return None
    try:
        phases = [p.get("phase", f"Phase {i + 1}") for i, p in enumerate(timeline)]
        spans = []
        cursor = 0.0
        for p in timeline:
            start, end = _parse_duration_span(p.get("duration", ""), fallback_start=cursor)
            spans.append((start, end))
            cursor = max(cursor, end)

        fig_height = max(1.8, 0.5 * len(phases) + 0.8)
        fig, ax = plt.subplots(figsize=(8.5, fig_height), dpi=150)
        y_positions = list(range(len(phases) - 1, -1, -1))
        for y, (start, end) in zip(y_positions, spans):
            ax.barh(y, end - start, left=start, height=0.55, color=ACCENT, edgecolor=NAVY, linewidth=0.8)

        ax.set_yticks(y_positions)
        ax.set_yticklabels(phases, fontsize=9)
        ax.set_xlabel("Project Week (durations normalized to a common scale)", fontsize=8.5, color="#444444")
        ax.grid(axis="x", color=LIGHT_GRID, linewidth=0.8, zorder=0)
        ax.set_axisbelow(True)
        for spine in ("top", "right", "left"):
            ax.spines[spine].set_visible(False)
        ax.tick_params(axis="both", labelsize=8.5, length=0)
        fig.tight_layout()

        buf = io.BytesIO()
        fig.savefig(buf, format="png", bbox_inches="tight")
        plt.close(fig)
        return buf.getvalue()
    except Exception:
        return None


def build_staffing_chart(staffing: list[dict]) -> bytes | None:
    """Horizontal bar chart of total hours by role, sorted largest-first. Returns None if there
    are no staffing lines to plot."""
    if not staffing:
        return None
    try:
        rows = sorted(staffing, key=lambda l: l.get("total_hours", 0), reverse=True)
        roles = [r["role"] for r in rows]
        hours = [r.get("total_hours", 0) for r in rows]

        fig_height = max(1.8, 0.45 * len(roles) + 0.8)
        fig, ax = plt.subplots(figsize=(8.5, fig_height), dpi=150)
        y_positions = list(range(len(roles) - 1, -1, -1))
        ax.barh(y_positions, hours, height=0.55, color=NAVY, edgecolor=NAVY)
        for y, h in zip(y_positions, hours):
            ax.text(h, y, f"  {h:,.0f} hrs", va="center", fontsize=8.5, color="#333333")

        ax.set_yticks(y_positions)
        ax.set_yticklabels(roles, fontsize=9)
        ax.set_xlabel("Total Hours", fontsize=8.5, color="#444444")
        ax.grid(axis="x", color=LIGHT_GRID, linewidth=0.8, zorder=0)
        ax.set_axisbelow(True)
        for spine in ("top", "right", "left"):
            ax.spines[spine].set_visible(False)
        ax.tick_params(axis="both", labelsize=8.5, length=0)
        ax.set_xlim(0, max(hours) * 1.18 if hours else 1)
        fig.tight_layout()

        buf = io.BytesIO()
        fig.savefig(buf, format="png", bbox_inches="tight")
        plt.close(fig)
        return buf.getvalue()
    except Exception:
        return None


def build_delivery_flow_diagram(phases: list[str]) -> bytes | None:
    """Left-to-right flow diagram of boxed phase names connected by arrows — a quick visual
    summary of the delivery approach, built from the same phase names as the timeline chart
    above (grounded in the actual project's timeline rather than a generic stock graphic).
    Wraps to a new row every 4 boxes so it stays readable with longer timelines. Returns None if
    there are no phases to draw."""
    if not phases:
        return None
    try:
        per_row = 4
        rows = [phases[i:i + per_row] for i in range(0, len(phases), per_row)]

        box_w, box_h, gap_x, gap_y = 2.0, 0.75, 0.55, 0.9
        n_cols = min(per_row, len(phases))
        fig_w = n_cols * box_w + (n_cols - 1) * gap_x + 1.0
        fig_h = len(rows) * box_h + (len(rows) - 1) * gap_y + 0.8

        fig, ax = plt.subplots(figsize=(min(fig_w, 10.5), fig_h), dpi=150)
        ax.set_xlim(0, fig_w)
        ax.set_ylim(0, fig_h)
        ax.axis("off")

        last_box_bottom_center = None  # (x, y) of the previous row's last box, for the row-wrap arrow
        for row_idx, row_phases in enumerate(rows):
            y = fig_h - 0.5 - row_idx * (box_h + gap_y)

            if last_box_bottom_center is not None:
                # Connects the LAST box of the previous row to the FIRST box of this row (reading
                # order), not straight down to whatever column happens to line up underneath —
                # a straight drop would silently skip every box in between whenever a row doesn't
                # end in the last column.
                first_box_top_center = (0.5 + box_w / 2, y)
                ax.annotate("", xy=first_box_top_center, xytext=last_box_bottom_center,
                            arrowprops=dict(arrowstyle="-|>", color=NAVY, linewidth=1.4,
                                             connectionstyle="arc3,rad=0.0"), zorder=2)

            for col_idx, phase_name in enumerate(row_phases):
                x = 0.5 + col_idx * (box_w + gap_x)
                ax.add_patch(plt.Rectangle((x, y - box_h), box_w, box_h,
                                            facecolor=ACCENT, edgecolor=NAVY, linewidth=1.2,
                                            zorder=2))
                label = phase_name if len(phase_name) <= 24 else phase_name[:21] + "..."
                ax.text(x + box_w / 2, y - box_h / 2, label, ha="center", va="center",
                        fontsize=8, color="white", weight="bold", zorder=3, wrap=True)
                is_last_in_row = col_idx == len(row_phases) - 1
                if not is_last_in_row:
                    ax.annotate("", xy=(x + box_w + gap_x, y - box_h / 2), xytext=(x + box_w, y - box_h / 2),
                                arrowprops=dict(arrowstyle="-|>", color=NAVY, linewidth=1.4), zorder=2)
                elif row_idx < len(rows) - 1:
                    last_box_bottom_center = (x + box_w / 2, y - box_h)

        fig.tight_layout()
        buf = io.BytesIO()
        fig.savefig(buf, format="png", bbox_inches="tight")
        plt.close(fig)
        return buf.getvalue()
    except Exception:
        return None
