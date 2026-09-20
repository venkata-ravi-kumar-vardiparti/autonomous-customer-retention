"""Phase 11: render experiments/run_ab.py's raw records into a Markdown +
chart comparison report.

Chart choices (see the dataviz skill's references, consulted before writing
this): each scalar metric (tokens, cost, latency, tool-call count) gets its
OWN single-axis grouped-bar chart - mixing different units on one y-axis
(a dual axis) is the #1 chart anti-pattern. Two categorical series (the two
arms) use the validated default palette's slot 1 (blue, bounded_pipeline)
and slot 2 (orange, open_harness), in that fixed order, every chart - never
recolored by rank. Every chart also gets a Markdown table twin (the
anti-patterns rule: "no table view" is itself an anti-pattern) so nothing
here gates on the image rendering correctly.

Usage:
    python -m experiments.report [--input PATH] [--output-dir DIR]
    (defaults: experiments/ab_results.json, experiments/report/)
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.axes import Axes  # noqa: E402

DEFAULT_INPUT_PATH = Path(__file__).resolve().parent / "ab_results.json"
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent / "report"

ARM_ORDER = ["bounded_pipeline", "open_harness"]
ARM_LABEL = {"bounded_pipeline": "Bounded pipeline", "open_harness": "Open harness"}
# Validated categorical pair (dataviz skill, references/palette.md slots 1/2):
# node scripts/validate_palette.js "#2a78d6,#eb6834" --mode light -> ALL CHECKS PASS
ARM_COLOR = {"bounded_pipeline": "#2a78d6", "open_harness": "#eb6834"}

SURFACE = "#fcfcfb"
PRIMARY_INK = "#0b0b0b"
SECONDARY_INK = "#52514e"
MUTED_INK = "#898781"
GRIDLINE = "#e1e0d9"
BASELINE = "#c3c2b7"

METRICS: list[tuple[str, str, str]] = [
    # (field, title, y-axis label)
    ("total_tokens", "Total tokens per call", "tokens"),
    ("total_cost_usd", "Total cost per call", "USD"),
    ("latency_ms", "Wall-clock latency per call", "ms"),
    ("tool_call_count", "Tool-call count per call", "calls"),
]


@dataclass(frozen=True)
class MetricStats:
    mean: float
    stdev: float
    n: int


def _load_records(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def _group_by_arm(records: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[record["arm"]].append(record)
    return grouped


def _metric_stats(records: list[dict[str, Any]], field: str) -> MetricStats:
    values = [r[field] for r in records if r["schema_valid"]]
    if not values:
        return MetricStats(mean=0.0, stdev=0.0, n=0)
    mean = statistics.mean(values)
    stdev = statistics.stdev(values) if len(values) > 1 else 0.0
    return MetricStats(mean=mean, stdev=stdev, n=len(values))


def _style_axes(ax: Axes) -> None:
    ax.set_facecolor(SURFACE)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_visible(False)
    ax.spines["bottom"].set_color(BASELINE)
    ax.spines["bottom"].set_linewidth(1)
    ax.yaxis.grid(True, color=GRIDLINE, linewidth=1, linestyle="-")
    ax.set_axisbelow(True)
    ax.tick_params(axis="both", colors=MUTED_INK, labelsize=9)
    ax.xaxis.label.set_color(SECONDARY_INK)
    ax.yaxis.label.set_color(SECONDARY_INK)


def _render_metric_chart(
    field: str, title: str, y_label: str, stats_by_arm: dict[str, MetricStats], output_path: Path
) -> None:
    fig, ax = plt.subplots(figsize=(4.5, 3.5), dpi=150)
    fig.patch.set_facecolor(SURFACE)
    _style_axes(ax)

    x_positions = list(range(len(ARM_ORDER)))
    means = [stats_by_arm[arm].mean for arm in ARM_ORDER]
    stdevs = [stats_by_arm[arm].stdev for arm in ARM_ORDER]
    colors = [ARM_COLOR[arm] for arm in ARM_ORDER]

    bars = ax.bar(
        x_positions,
        means,
        width=0.5,
        color=colors,
        yerr=stdevs,
        capsize=4,
        ecolor=SECONDARY_INK,
        error_kw={"elinewidth": 1, "capthick": 1},
    )
    ax.bar_label(
        bars,
        labels=[f"{m:,.3g}" for m in means],
        padding=6,
        color=PRIMARY_INK,
        fontsize=9,
    )

    ax.set_xticks(x_positions)
    ax.set_xticklabels([ARM_LABEL[arm] for arm in ARM_ORDER])
    ax.set_ylabel(y_label)
    ax.set_title(title, color=PRIMARY_INK, fontsize=11, loc="left", pad=12)
    ax.set_ylim(bottom=0)

    fig.tight_layout()
    fig.savefig(output_path, facecolor=SURFACE)
    plt.close(fig)


def _render_reproducibility_chart(
    records: list[dict[str, Any]], output_path: Path
) -> dict[str, dict[str, int]]:
    """Number of DISTINCT top_offer_id values observed per (arm, fixture)
    across its repeats - 1 means every repeat agreed (fully reproducible),
    >1 means the top offer changed across identical inputs. This is the
    direct, chart-appropriate stand-in for "output variance": the raw
    top_offer_id values are categorical text, not a magnitude, so they
    render as a count here and as a table in the Markdown report."""
    by_fixture_arm: dict[str, dict[str, set[str | None]]] = defaultdict(lambda: defaultdict(set))
    for record in records:
        if not record["schema_valid"]:
            continue
        by_fixture_arm[record["fixture"]][record["arm"]].add(record["top_offer_id"])

    fixtures = sorted(by_fixture_arm)
    distinct_counts: dict[str, dict[str, int]] = {
        fixture: {arm: len(by_fixture_arm[fixture].get(arm, set())) for arm in ARM_ORDER}
        for fixture in fixtures
    }

    fig, ax = plt.subplots(figsize=(9, 4), dpi=150)
    fig.patch.set_facecolor(SURFACE)
    _style_axes(ax)

    bar_width = 0.35
    x_positions = list(range(len(fixtures)))
    for offset, arm in zip((-bar_width / 2, bar_width / 2), ARM_ORDER, strict=True):
        heights = [distinct_counts[fixture][arm] for fixture in fixtures]
        ax.bar(
            [x + offset for x in x_positions],
            heights,
            width=bar_width,
            color=ARM_COLOR[arm],
            label=ARM_LABEL[arm],
        )

    ax.set_xticks(x_positions)
    ax.set_xticklabels(fixtures, rotation=45, ha="right", fontsize=7)
    ax.set_ylabel("distinct top offer IDs observed")
    ax.set_title(
        "Reproducibility per fixture (1 = identical top offer every repeat)",
        color=PRIMARY_INK,
        fontsize=11,
        loc="left",
        pad=12,
    )
    max_observed = max((v for d in distinct_counts.values() for v in d.values()), default=1)
    ax.set_yticks(range(0, max(2, max_observed) + 1))
    ax.legend(frameon=False, labelcolor=SECONDARY_INK, fontsize=9, loc="upper right")

    fig.tight_layout()
    fig.savefig(output_path, facecolor=SURFACE)
    plt.close(fig)

    return distinct_counts


def _markdown_metric_table(stats_by_arm: dict[str, MetricStats], field: str, y_label: str) -> str:
    lines = [
        f"| Arm | mean ({y_label}) | stddev | n |",
        "|---|---|---|---|",
    ]
    for arm in ARM_ORDER:
        s = stats_by_arm[arm]
        lines.append(f"| {ARM_LABEL[arm]} | {s.mean:,.4g} | {s.stdev:,.4g} | {s.n} |")
    return "\n".join(lines)


def _schema_and_policy_summary(records: list[dict[str, Any]]) -> tuple[str, bool, bool]:
    lines = ["| Arm | runs | schema-valid | policy violations | errors |", "|---|---|---|---|---|"]
    any_invalid = False
    any_violation = False
    for arm in ARM_ORDER:
        arm_records = [r for r in records if r["arm"] == arm]
        n = len(arm_records)
        valid = sum(1 for r in arm_records if r["schema_valid"])
        violations = sum(1 for r in arm_records if r["policy_violation"])
        errors = [r["error"] for r in arm_records if r["error"]]
        if valid < n:
            any_invalid = True
        if violations:
            any_violation = True
        lines.append(
            f"| {ARM_LABEL[arm]} | {n} | {valid}/{n} | {violations} | {len(errors)} |"
        )
        for error in errors[:5]:
            lines.append(f"|   | *(sample error)* {error} | | | |")
    return "\n".join(lines), any_invalid, any_violation


def build_report(records: list[dict[str, Any]], output_dir: Path) -> str:
    output_dir.mkdir(parents=True, exist_ok=True)
    grouped = _group_by_arm(records)

    sections: list[str] = []
    sections.append("# ChurnGuard Phase 11: bounded pipeline vs open harness\n")
    sections.append(
        "**Expected finding (stated before looking at the data, per the phase brief):** "
        "the bounded pipeline should be cheaper, flatter in context growth and far more "
        "reproducible; the open harness should occasionally surface evidence the fixed "
        "plan missed. What follows is what was actually measured, including anywhere it "
        "contradicts this.\n"
    )

    schema_table, any_invalid, any_violation = _schema_and_policy_summary(records)
    sections.append("## Schema validity & policy violations (ACCEPTANCE 2/3/4)\n")
    sections.append(schema_table + "\n")
    if any_invalid:
        sections.append(
            "**FINDING: at least one arm produced schema-invalid output or an unhandled "
            "exception.** See the sampled errors above.\n"
        )
    else:
        sections.append("No schema-invalid output in either arm.\n")
    if any_violation:
        sections.append(
            "**HEADLINE FINDING: a policy violation occurred.** A policy-blocked candidate "
            "was offered as a live recommendation in at least one run - see the table above "
            "for which arm. This should never happen in either arm; investigate before "
            "trusting any other number in this report.\n"
        )
    else:
        sections.append(
            "Zero policy violations in both arms across all runs - the policy engine held "
            "as a hard filter regardless of which arm gathered the evidence.\n"
        )

    sections.append("## Cost, latency and context (mean ± stddev)\n")
    for field, title, y_label in METRICS:
        stats_by_arm = {arm: _metric_stats(grouped.get(arm, []), field) for arm in ARM_ORDER}
        chart_path = output_dir / f"{field}.png"
        _render_metric_chart(field, title, y_label, stats_by_arm, chart_path)
        sections.append(f"### {title}\n")
        sections.append(f"![{title}]({chart_path.name})\n")
        sections.append(_markdown_metric_table(stats_by_arm, field, y_label) + "\n")

    sections.append("## Output variance / reproducibility\n")
    repro_chart_path = output_dir / "reproducibility.png"
    distinct_counts = _render_reproducibility_chart(records, repro_chart_path)
    sections.append(f"![Reproducibility per fixture]({repro_chart_path.name})\n")
    repro_lines = ["| Fixture | Bounded pipeline | Open harness |", "|---|---|---|"]
    for fixture, per_arm in sorted(distinct_counts.items()):
        bounded_n = per_arm.get("bounded_pipeline", 0)
        harness_n = per_arm.get("open_harness", 0)
        repro_lines.append(f"| {fixture} | {bounded_n} | {harness_n} |")
    sections.append("\n".join(repro_lines) + "\n")

    bounded_variances = [v.get("bounded_pipeline", 0) for v in distinct_counts.values()]
    harness_variances = [v.get("open_harness", 0) for v in distinct_counts.values()]
    max_bounded_variance = max(bounded_variances, default=0)
    max_harness_variance = max(harness_variances, default=0)
    sections.append("## Conclusion\n")
    if max_bounded_variance <= 1 and max_harness_variance > max_bounded_variance:
        sections.append(
            "**Confirms the expected finding**: the bounded pipeline's top offer never "
            "changed across repeats of the same input; the open harness's did for at "
            "least one fixture.\n"
        )
    elif max_bounded_variance <= 1 and max_harness_variance <= 1:
        sections.append(
            "**Partially contradicts the expected finding**: with this run's model "
            "doubles/sample size, the open harness was JUST as reproducible as the "
            "bounded pipeline (both arms: 1 distinct top offer per fixture). Re-run with "
            "a larger --n or real models before concluding the two arms are equally "
            "reproducible in production.\n"
        )
    else:
        sections.append(
            "**Contradicts the expected finding**: the bounded pipeline itself showed "
            "more than one top offer across repeats of an identical input, which should "
            "not happen (it holds no model-driven branching after evidence-gathering) - "
            "investigate before trusting the comparison.\n"
        )

    return "\n".join(sections)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    records = _load_records(args.input)
    report_markdown = build_report(records, args.output_dir)

    report_path = args.output_dir / "REPORT.md"
    report_path.write_text(report_markdown, encoding="utf-8")
    print(f"wrote report to {report_path}")


if __name__ == "__main__":
    main()
