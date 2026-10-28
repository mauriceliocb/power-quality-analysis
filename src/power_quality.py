"""
Automated power quality analysis from a power analyser export.

The script reads the measurement CSV, computes the indicators that used to be
worked out by hand in a spreadsheet, sizes the capacitor bank required to reach
the target power factor, renders the charts and writes a Markdown report.

Usage:
    python src/power_quality.py data/sample_measurements.csv --output examples
    python src/power_quality.py data/measurements.csv --target-pf 0.95 --tariff 0.62

Expected CSV columns (names configurable in COLUMNS):
    timestamp, V1, V2, V3, I1, I2, I3, P_kW, Q_kvar, S_kVA, PF,
    THD_V_pct, THD_I_pct
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Parameters
# ---------------------------------------------------------------------------

COLUMNS = {
    "time": "timestamp",
    "voltages": ["V1", "V2", "V3"],
    "currents": ["I1", "I2", "I3"],
    "active": "P_kW",
    "reactive": "Q_kvar",
    "apparent": "S_kVA",
    "pf": "PF",
    "thd_v": "THD_V_pct",
    "thd_i": "THD_I_pct",
}

# Regulatory references used by the report alerts (Brazilian grid code).
MIN_POWER_FACTOR = 0.92  # PRODIST, Module 8
THD_V_LIMIT_PCT = 8.0  # total voltage harmonic distortion, low voltage
VOLTAGE_UNBALANCE_LIMIT_PCT = 3.0

# Palette (light surface): blue, orange, teal.
COLOR = {
    "series1": "#2a78d6",
    "series2": "#eb6834",
    "series3": "#1baf7a",
    "limit": "#52514e",
    "text": "#0b0b0b",
    "text2": "#52514e",
    "grid": "#dcdcd6",
    "surface": "#fcfcfb",
}


@dataclass
class Indicators:
    start: pd.Timestamp
    end: pd.Timestamp
    step_h: float
    energy_kwh: float
    reactive_energy_kvarh: float
    peak_demand_kw: float
    average_demand_kw: float
    load_factor: float
    average_pf: float
    hours_below_pf: float
    average_voltage: float
    max_unbalance_pct: float
    max_thd_v: float
    max_thd_i: float
    average_thd_i: float
    k_factor: float
    capacitor_bank_kvar: float
    estimated_cost: float | None


# ---------------------------------------------------------------------------
# Loading and computation
# ---------------------------------------------------------------------------


def load(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=[COLUMNS["time"]])
    df = df.sort_values(COLUMNS["time"]).reset_index(drop=True)

    missing = [c for c in _expected_columns() if c not in df.columns]
    if missing:
        raise SystemExit(f"Missing columns in the input file: {', '.join(missing)}")

    return df


def _expected_columns() -> list[str]:
    expected = [COLUMNS["time"], COLUMNS["active"], COLUMNS["reactive"]]
    expected += COLUMNS["voltages"] + COLUMNS["currents"]
    expected += [COLUMNS["pf"], COLUMNS["thd_v"], COLUMNS["thd_i"]]
    return expected


def step_hours(df: pd.DataFrame) -> float:
    deltas = df[COLUMNS["time"]].diff().dropna()
    if deltas.empty:
        return 0.25
    return float(deltas.mode().iloc[0].total_seconds() / 3600)


def voltage_unbalance(df: pd.DataFrame) -> pd.Series:
    """Percentage unbalance from the maximum deviation against the phase average."""
    v = df[COLUMNS["voltages"]]
    mean = v.mean(axis=1)
    deviation = (v.sub(mean, axis=0)).abs().max(axis=1)
    return 100 * deviation / mean


def k_factor(thd_i_pct: float, max_order: int = 25) -> float:
    """
    Transformer K-factor estimated from the current THD.

    Assumes a typical three-phase non-linear load spectrum (odd harmonics that
    are not multiples of three, with amplitude proportional to 1/h). This is a
    pre-sizing estimate and does not replace a measured harmonic spectrum.
    """
    orders = [h for h in range(3, max_order + 1, 2) if h % 3 != 0]
    weights = np.array([1 / h for h in orders])
    weights = weights / np.sqrt((weights**2).sum())  # normalised spectrum
    ih = (thd_i_pct / 100) * weights  # harmonic currents in pu of the fundamental
    i1 = 1.0
    i_rms2 = i1**2 + (ih**2).sum()
    numerator = i1**2 + sum((ihn**2) * (h**2) for ihn, h in zip(ih, orders))
    return float(numerator / i_rms2)


def capacitor_bank_kvar(p_kw: float, current_pf: float, target_pf: float) -> float:
    """Reactive power required to correct the power factor."""
    if current_pf >= target_pf:
        return 0.0
    tan = lambda pf: np.tan(np.arccos(pf))  # noqa: E731
    return float(p_kw * (tan(current_pf) - tan(target_pf)))


def compute(df: pd.DataFrame, target_pf: float, tariff: float | None) -> Indicators:
    h = step_hours(df)
    p = df[COLUMNS["active"]]
    q = df[COLUMNS["reactive"]]
    pf = df[COLUMNS["pf"]]

    energy_kwh = float(p.sum() * h)
    reactive_kvarh = float(q.sum() * h)
    peak_demand = float(p.max())
    average_demand = float(p.mean())

    # Period power factor: active energy over apparent energy.
    average_pf = float(energy_kwh / np.hypot(energy_kwh, reactive_kvarh))
    hours_below = float((pf < MIN_POWER_FACTOR).sum() * h)

    unbalance = voltage_unbalance(df)
    average_thd_i = float(df[COLUMNS["thd_i"]].mean())

    # Size the bank on the heaviest load band, which is the critical condition.
    cutoff = p.quantile(0.90)
    heavy_load = df[p >= cutoff]
    p_sizing = float(heavy_load[COLUMNS["active"]].mean())
    pf_sizing = float(heavy_load[COLUMNS["pf"]].mean())
    kvar = capacitor_bank_kvar(p_sizing, pf_sizing, target_pf)

    return Indicators(
        start=df[COLUMNS["time"]].iloc[0],
        end=df[COLUMNS["time"]].iloc[-1],
        step_h=h,
        energy_kwh=energy_kwh,
        reactive_energy_kvarh=reactive_kvarh,
        peak_demand_kw=peak_demand,
        average_demand_kw=average_demand,
        load_factor=average_demand / peak_demand if peak_demand else 0.0,
        average_pf=average_pf,
        hours_below_pf=hours_below,
        average_voltage=float(df[COLUMNS["voltages"]].to_numpy().mean()),
        max_unbalance_pct=float(unbalance.max()),
        max_thd_v=float(df[COLUMNS["thd_v"]].max()),
        max_thd_i=float(df[COLUMNS["thd_i"]].max()),
        average_thd_i=average_thd_i,
        k_factor=k_factor(average_thd_i),
        capacitor_bank_kvar=kvar,
        estimated_cost=energy_kwh * tariff if tariff else None,
    )


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------


def _style(ax, title: str, y_label: str) -> None:
    ax.set_title(title, color=COLOR["text"], fontsize=12, loc="left", pad=12)
    ax.set_ylabel(y_label, color=COLOR["text2"], fontsize=9)
    ax.grid(axis="y", color=COLOR["grid"], linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(COLOR["grid"])
    ax.tick_params(colors=COLOR["text2"], labelsize=9, length=0)


def _save(fig, target: Path) -> Path:
    fig.tight_layout()
    fig.savefig(target, dpi=160, facecolor=COLOR["surface"])
    plt.close(fig)
    return target


def chart_load_curve(df: pd.DataFrame, output: Path) -> Path:
    fig, ax = plt.subplots(figsize=(9, 3.4), facecolor=COLOR["surface"])
    ax.set_facecolor(COLOR["surface"])
    ax.plot(df[COLUMNS["time"]], df[COLUMNS["active"]], color=COLOR["series1"], linewidth=1.6,
            label="Active power (kW)")
    ax.plot(df[COLUMNS["time"]], df[COLUMNS["reactive"]], color=COLOR["series2"], linewidth=1.6,
            label="Reactive power (kvar)")
    _style(ax, "Load curve over the measured period", "Power")
    ax.legend(frameon=False, fontsize=9, labelcolor=COLOR["text2"], loc="upper right", ncols=2)
    return _save(fig, output / "load_curve.png")


def chart_power_factor(df: pd.DataFrame, output: Path) -> Path:
    fig, ax = plt.subplots(figsize=(9, 3.2), facecolor=COLOR["surface"])
    ax.set_facecolor(COLOR["surface"])
    ax.plot(df[COLUMNS["time"]], df[COLUMNS["pf"]], color=COLOR["series1"], linewidth=1.6)
    ax.axhline(MIN_POWER_FACTOR, color=COLOR["limit"], linewidth=1.2, linestyle="--")
    ax.annotate(
        f"regulatory minimum {MIN_POWER_FACTOR:.2f}",
        xy=(df[COLUMNS["time"]].iloc[2], MIN_POWER_FACTOR),
        xytext=(0, -16), textcoords="offset points",
        color=COLOR["text2"], fontsize=9,
    )
    _style(ax, "Power factor over the measured period", "Power factor")
    ax.set_ylim(0.5, 1.02)
    return _save(fig, output / "power_factor.png")


def chart_hourly_profile(df: pd.DataFrame, output: Path) -> Path:
    by_hour = df.groupby(df[COLUMNS["time"]].dt.hour)[COLUMNS["active"]].mean()
    fig, ax = plt.subplots(figsize=(9, 3.2), facecolor=COLOR["surface"])
    ax.set_facecolor(COLOR["surface"])
    ax.bar(by_hour.index, by_hour.values, color=COLOR["series1"], width=0.74)
    _style(ax, "Average demand by hour of day", "Average active power (kW)")
    ax.set_xlabel("Hour", color=COLOR["text2"], fontsize=9)
    ax.set_xticks(range(0, 24, 2))
    return _save(fig, output / "hourly_profile.png")


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def alerts(ind: Indicators) -> list[str]:
    items = []
    if ind.average_pf < MIN_POWER_FACTOR:
        items.append(
            f"Average power factor of {ind.average_pf:.3f}, below the {MIN_POWER_FACTOR:.2f} "
            f"minimum. {ind.hours_below_pf:,.0f} h of the measurement are non-compliant and "
            "subject to excess reactive energy charges."
        )
    if ind.max_unbalance_pct > VOLTAGE_UNBALANCE_LIMIT_PCT:
        items.append(
            f"Peak voltage unbalance of {ind.max_unbalance_pct:.2f}%, above the "
            f"{VOLTAGE_UNBALANCE_LIMIT_PCT:.1f}% reference limit."
        )
    if ind.max_thd_v > THD_V_LIMIT_PCT:
        items.append(
            f"Voltage harmonic distortion reaching {ind.max_thd_v:.2f}%, above the "
            f"{THD_V_LIMIT_PCT:.1f}% limit for low voltage."
        )
    if ind.k_factor > 4:
        items.append(
            f"Estimated K-factor of {ind.k_factor:.1f}. The transformer should be specified "
            "for non-linear load or have its rating reviewed."
        )
    if not items:
        items.append("No indicator outside the reference limits for the measured period.")
    return items


def write_report(ind: Indicators, charts: list[Path], output: Path, target_pf: float) -> Path:
    target = output / "report.md"
    days = (ind.end - ind.start).total_seconds() / 86400

    lines = [
        "# Power quality report",
        "",
        f"**Measured period:** {ind.start:%Y-%m-%d %H:%M} to {ind.end:%Y-%m-%d %H:%M} "
        f"({days:.1f} days, {ind.step_h * 60:.0f} min integration)",
        "",
        "## Indicators",
        "",
        "| Indicator | Value |",
        "| --- | --- |",
        f"| Active energy | {ind.energy_kwh:,.0f} kWh |",
        f"| Reactive energy | {ind.reactive_energy_kvarh:,.0f} kvarh |",
        f"| Peak demand | {ind.peak_demand_kw:,.1f} kW |",
        f"| Average demand | {ind.average_demand_kw:,.1f} kW |",
        f"| Load factor | {ind.load_factor:.2f} |",
        f"| Average power factor | {ind.average_pf:.3f} |",
        f"| Hours with PF below {MIN_POWER_FACTOR:.2f} | {ind.hours_below_pf:,.0f} h |",
        f"| Average voltage | {ind.average_voltage:.1f} V |",
        f"| Peak voltage unbalance | {ind.max_unbalance_pct:.2f} % |",
        f"| Peak voltage THD | {ind.max_thd_v:.2f} % |",
        f"| Current THD, average / peak | {ind.average_thd_i:.2f} % / {ind.max_thd_i:.2f} % |",
        f"| Estimated transformer K-factor | {ind.k_factor:.1f} |",
    ]
    if ind.estimated_cost is not None:
        lines.append(f"| Estimated energy cost for the period | BRL {ind.estimated_cost:,.2f} |")

    lines += [
        "",
        "## Power factor correction",
        "",
        f"Raising the power factor to the {target_pf:.2f} target at the heaviest load band "
        f"requires a capacitor bank of **{ind.capacitor_bank_kvar:,.0f} kvar**.",
        "",
        "Sizing uses the mean of the top decile of the load curve, which is the critical "
        "condition. An automatic stepped bank is recommended to avoid overcorrection during "
        "light load periods.",
        "",
        "## Findings",
        "",
    ]
    lines += [f"- {item}" for item in alerts(ind)]
    lines += ["", "## Charts", ""]
    lines += [f"![{c.stem}]({c.name})" for c in charts]
    lines += [
        "",
        "---",
        "",
        "Report generated automatically by `power_quality.py`.",
        "",
    ]

    target.write_text("\n".join(lines), encoding="utf-8")
    return target


# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Power quality analysis.")
    parser.add_argument("input", type=Path, help="CSV exported from the analyser")
    parser.add_argument("--output", type=Path, default=Path("examples"), help="output folder")
    parser.add_argument("--target-pf", type=float, default=0.95, help="target power factor")
    parser.add_argument("--tariff", type=float, default=None, help="energy tariff in BRL/kWh")
    args = parser.parse_args()

    args.output.mkdir(parents=True, exist_ok=True)

    df = load(args.input)
    ind = compute(df, args.target_pf, args.tariff)
    charts = [
        chart_load_curve(df, args.output),
        chart_power_factor(df, args.output),
        chart_hourly_profile(df, args.output),
    ]
    report = write_report(ind, charts, args.output, args.target_pf)

    print(f"{len(df)} records analysed.")
    print(f"Average power factor: {ind.average_pf:.3f}")
    print(f"Suggested capacitor bank: {ind.capacitor_bank_kvar:,.0f} kvar")
    print(f"Report: {report}")


if __name__ == "__main__":
    main()
