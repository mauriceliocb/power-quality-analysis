"""
Tariff study for a Brazilian Group A consumer, built from the same measurement
dataset.

It splits energy and demand between peak and off-peak windows, searches for the
cheapest contracted demand under each tariff option and compares the monthly
bill of the Green and Blue tariffs, including the demand overrun penalty.

Green tariff: a single contracted demand, with a much higher energy price during
the peak window. Blue tariff: separate contracted demands for peak and off-peak,
with a smaller gap between energy prices.

Usage:
    python src/tariff_study.py data/sample_measurements.csv --output examples
    python src/tariff_study.py data/measurements.csv --tariffs config/tariffs.json
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import pandas as pd

from power_quality import COLOR, COLUMNS, step_hours

DAYS_PER_MONTH = 30


@dataclass
class Bill:
    tariff: str
    contracted_demand_kw: float | dict[str, float]
    energy_cost: float
    demand_cost: float
    overrun_cost: float

    @property
    def total(self) -> float:
        return self.energy_cost + self.demand_cost + self.overrun_cost


@dataclass
class Measurement:
    peak_energy_kwh: float
    off_peak_energy_kwh: float
    peak_demand_kw: float
    off_peak_demand_kw: float
    days: float

    def to_monthly(self) -> "Measurement":
        """Projects the measured energy to a 30-day month; demand is the maximum."""
        factor = DAYS_PER_MONTH / self.days
        return Measurement(
            peak_energy_kwh=self.peak_energy_kwh * factor,
            off_peak_energy_kwh=self.off_peak_energy_kwh * factor,
            peak_demand_kw=self.peak_demand_kw,
            off_peak_demand_kw=self.off_peak_demand_kw,
            days=DAYS_PER_MONTH,
        )


# ---------------------------------------------------------------------------


def load_tariffs(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def split_windows(df: pd.DataFrame, tariffs: dict) -> Measurement:
    h = step_hours(df)
    ts = df[COLUMNS["time"]]
    window = tariffs["peak_window"]

    in_peak = (ts.dt.hour >= window["start_hour"]) & (ts.dt.hour < window["end_hour"])
    if window.get("weekdays_only", True):
        in_peak &= ts.dt.dayofweek < 5

    p = df[COLUMNS["active"]]
    days = (ts.iloc[-1] - ts.iloc[0]).total_seconds() / 86400

    return Measurement(
        peak_energy_kwh=float(p[in_peak].sum() * h),
        off_peak_energy_kwh=float(p[~in_peak].sum() * h),
        peak_demand_kw=float(p[in_peak].max()) if in_peak.any() else 0.0,
        off_peak_demand_kw=float(p[~in_peak].max()),
        days=max(days, h / 24),
    )


def demand_cost(measured_kw: float, contracted_kw: float, tariff_kw: float,
                tariffs: dict) -> tuple[float, float]:
    """Returns (billed demand cost, overrun penalty)."""
    tolerance = tariffs["overrun_tolerance"]
    factor = tariffs["overrun_penalty_factor"]

    excess = max(0.0, measured_kw - contracted_kw * tolerance)
    if excess:
        cost = contracted_kw * tariff_kw + (measured_kw - contracted_kw - excess) * tariff_kw
        penalty = excess * tariff_kw * factor
    else:
        cost = max(contracted_kw, measured_kw) * tariff_kw if measured_kw > contracted_kw \
            else contracted_kw * tariff_kw
        penalty = 0.0

    return cost, penalty


def green_bill(m: Measurement, contracted_kw: float, tariffs: dict) -> Bill:
    t = tariffs["green"]
    energy = (m.peak_energy_kwh * t["peak_energy_brl_kwh"]
              + m.off_peak_energy_kwh * t["off_peak_energy_brl_kwh"])
    measured = max(m.peak_demand_kw, m.off_peak_demand_kw)
    demand, penalty = demand_cost(measured, contracted_kw, t["demand_brl_kw"], tariffs)
    return Bill("Green", contracted_kw, energy, demand, penalty)


def blue_bill(m: Measurement, contracted_peak: float, contracted_off_peak: float,
              tariffs: dict) -> Bill:
    t = tariffs["blue"]
    energy = (m.peak_energy_kwh * t["peak_energy_brl_kwh"]
              + m.off_peak_energy_kwh * t["off_peak_energy_brl_kwh"])
    d_peak, penalty_peak = demand_cost(m.peak_demand_kw, contracted_peak,
                                       t["peak_demand_brl_kw"], tariffs)
    d_off, penalty_off = demand_cost(m.off_peak_demand_kw, contracted_off_peak,
                                     t["off_peak_demand_brl_kw"], tariffs)
    return Bill(
        "Blue",
        {"peak": contracted_peak, "off_peak": contracted_off_peak},
        energy,
        d_peak + d_off,
        penalty_peak + penalty_off,
    )


def optimise(m: Measurement, tariffs: dict) -> tuple[Bill, Bill]:
    """Searches for the cheapest contracted demand under each tariff."""
    def grid(measured: float) -> list[float]:
        top = max(int(measured * 1.3) + 2, 5)
        return [float(kw) for kw in range(1, top)]

    green_measured = max(m.peak_demand_kw, m.off_peak_demand_kw)
    green = min((green_bill(m, kw, tariffs) for kw in grid(green_measured)),
                key=lambda b: b.total)

    best_peak = min(
        grid(m.peak_demand_kw),
        key=lambda kw: sum(demand_cost(m.peak_demand_kw, kw,
                                       tariffs["blue"]["peak_demand_brl_kw"], tariffs)),
    )
    best_off_peak = min(
        grid(m.off_peak_demand_kw),
        key=lambda kw: sum(demand_cost(m.off_peak_demand_kw, kw,
                                       tariffs["blue"]["off_peak_demand_brl_kw"], tariffs)),
    )
    blue = blue_bill(m, best_peak, best_off_peak, tariffs)

    return green, blue


# ---------------------------------------------------------------------------


def comparison_chart(green: Bill, blue: Bill, output: Path) -> Path:
    fig, ax = plt.subplots(figsize=(7.2, 3.8), facecolor=COLOR["surface"])
    ax.set_facecolor(COLOR["surface"])

    labels = ["Green", "Blue"]
    energy = [green.energy_cost, blue.energy_cost]
    demand = [green.demand_cost, blue.demand_cost]
    penalty = [green.overrun_cost, blue.overrun_cost]

    edge = {"edgecolor": COLOR["surface"], "linewidth": 2}
    ax.bar(labels, energy, color=COLOR["series1"], width=0.38, label="Energy", **edge)
    ax.bar(labels, demand, bottom=energy, color=COLOR["series2"], width=0.38,
           label="Demand", **edge)
    base = [e + d for e, d in zip(energy, demand)]
    if any(penalty):
        ax.bar(labels, penalty, bottom=base, color=COLOR["series3"], width=0.38,
               label="Overrun", **edge)

    for i, bill in enumerate([green, blue]):
        ax.annotate(
            f"BRL {bill.total:,.2f}",
            xy=(i, bill.total), xytext=(0, 8), textcoords="offset points",
            ha="center", color=COLOR["text"], fontsize=10, fontweight="bold",
        )

    ax.set_title("Estimated monthly bill by tariff", color=COLOR["text"], fontsize=12,
                 loc="left", pad=12)
    ax.set_ylabel("BRL per month", color=COLOR["text2"], fontsize=9)
    ax.grid(axis="y", color=COLOR["grid"], linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(COLOR["grid"])
    ax.tick_params(colors=COLOR["text2"], labelsize=9, length=0)
    ax.margins(y=0.22)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:,.0f}"))
    ax.legend(frameon=False, fontsize=9, labelcolor=COLOR["text2"], ncols=3,
              loc="lower center", bbox_to_anchor=(0.5, -0.3))

    fig.tight_layout()
    target = output / "tariff_comparison.png"
    fig.savefig(target, dpi=160, facecolor=COLOR["surface"])
    plt.close(fig)
    return target


def write_report(m: Measurement, green: Bill, blue: Bill, tariffs: dict,
                 chart: Path, output: Path, measured_days: float) -> Path:
    target = output / "tariff_study.md"
    saving = abs(green.total - blue.total)
    best = green if green.total <= blue.total else blue
    worst = blue if best is green else green
    window = tariffs["peak_window"]

    lines = [
        "# Tariff study",
        "",
        f"**Basis:** {measured_days:.1f} days of measurement, projected to a "
        f"{DAYS_PER_MONTH}-day month.",
        f"**Utility:** {tariffs['utility']} · **Subgroup:** {tariffs['subgroup']} · "
        f"**Tariffs effective:** {tariffs['effective_year']}",
        f"**Peak window:** {window['start_hour']}:00 to {window['end_hour']}:00"
        + (", weekdays only." if window.get("weekdays_only", True) else "."),
        "",
        "## Energy and demand by window",
        "",
        "| Quantity | Peak | Off-peak |",
        "| --- | --- | --- |",
        f"| Projected monthly energy | {m.peak_energy_kwh:,.0f} kWh | "
        f"{m.off_peak_energy_kwh:,.0f} kWh |",
        f"| Recorded peak demand | {m.peak_demand_kw:,.1f} kW | {m.off_peak_demand_kw:,.1f} kW |",
        "",
        "## Tariff comparison",
        "",
        "| Component | Green | Blue |",
        "| --- | --- | --- |",
        f"| Energy | BRL {green.energy_cost:,.2f} | BRL {blue.energy_cost:,.2f} |",
        f"| Demand | BRL {green.demand_cost:,.2f} | BRL {blue.demand_cost:,.2f} |",
        f"| Overrun penalty | BRL {green.overrun_cost:,.2f} | BRL {blue.overrun_cost:,.2f} |",
        f"| **Monthly total** | **BRL {green.total:,.2f}** | **BRL {blue.total:,.2f}** |",
        "",
        "## Cheapest contracted demand",
        "",
        f"- **Green:** {float(green.contracted_demand_kw):,.0f} kW (single contract).",
        f"- **Blue:** {blue.contracted_demand_kw['peak']:,.0f} kW at peak and "
        f"{blue.contracted_demand_kw['off_peak']:,.0f} kW off-peak.",
        "",
        "The search sweeps the possible contracted demands and keeps the cheapest total, "
        "taking into account that the overrun penalty only applies above "
        f"{(tariffs['overrun_tolerance'] - 1) * 100:,.0f}% of the contracted value, at "
        f"{tariffs['overrun_penalty_factor']:,.0f}× the demand tariff.",
        "",
        "## Conclusion",
        "",
        f"The **{best.tariff}** tariff is the better fit for this load profile, with an "
        f"estimated saving of **BRL {saving:,.2f} per month** (BRL {saving * 12:,.2f} per "
        f"year) against the {worst.tariff} tariff.",
        "",
        "## Chart",
        "",
        f"![tariff_comparison]({chart.name})",
        "",
        "---",
        "",
        "Study generated automatically by `tariff_study.py`. Tariffs are read from "
        "`config/tariffs.json` and must be kept in line with the utility's current tariff "
        "resolution.",
        "",
    ]

    target.write_text("\n".join(lines), encoding="utf-8")
    return target


# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Tariff study for a Group A consumer.")
    parser.add_argument("input", type=Path, help="CSV exported from the analyser")
    parser.add_argument("--output", type=Path, default=Path("examples"), help="output folder")
    parser.add_argument("--tariffs", type=Path, default=Path("config/tariffs.json"),
                        help="tariff configuration file")
    args = parser.parse_args()

    args.output.mkdir(parents=True, exist_ok=True)

    tariffs = load_tariffs(args.tariffs)
    df = pd.read_csv(args.input, parse_dates=[COLUMNS["time"]]).sort_values(COLUMNS["time"])

    measured = split_windows(df, tariffs)
    monthly = measured.to_monthly()
    green, blue = optimise(monthly, tariffs)

    chart = comparison_chart(green, blue, args.output)
    report = write_report(monthly, green, blue, tariffs, chart, args.output, measured.days)

    best = green if green.total <= blue.total else blue
    print(f"Green: BRL {green.total:,.2f}/month · Blue: BRL {blue.total:,.2f}/month")
    print(f"Recommended tariff: {best.tariff}")
    print(f"Study: {report}")


if __name__ == "__main__":
    main()
