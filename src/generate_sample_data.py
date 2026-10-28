"""
Generates a synthetic measurement dataset in the format exported by power
quality analysers (one record every 15 minutes, over one week).

It exists so the repository can be run end to end without exposing real client
data. The values imitate a low-voltage industrial installation with a poor
power factor during production hours.

Usage:
    python src/generate_sample_data.py data/sample_measurements.csv
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

STEP_MIN = 15
DAYS = 7
NOMINAL_VOLTAGE = 220.0


def generate(seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)

    start = pd.Timestamp("2026-03-02 00:00:00")
    periods = int(DAYS * 24 * 60 / STEP_MIN)
    ts = pd.date_range(start, periods=periods, freq=f"{STEP_MIN}min")

    hour = ts.hour + ts.minute / 60
    weekday = ts.dayofweek < 5
    # Load profile: production plateau from 7am to 5pm on weekdays.
    production = weekday & (hour >= 7) & (hour < 17)

    base_kw = np.where(production, 180.0, 45.0)
    base_kw = base_kw + rng.normal(0, 8, periods)
    # Two motor-start peaks per working day.
    peak = weekday & (np.isclose(hour, 7.0) | np.isclose(hour, 13.0))
    base_kw = np.where(peak, base_kw * 1.35, base_kw)
    p_kw = np.clip(base_kw, 20, None)

    # Low power factor during the production plateau (induction motors).
    pf = np.where(production, rng.normal(0.78, 0.02, periods), rng.normal(0.89, 0.02, periods))
    pf = np.clip(pf, 0.60, 0.99)

    s_kva = p_kw / pf
    q_kvar = np.sqrt(np.clip(s_kva**2 - p_kw**2, 0, None))

    voltages = {}
    for i, offset in enumerate([0.0, -1.8, 1.2], start=1):
        v = NOMINAL_VOLTAGE + offset + rng.normal(0, 1.1, periods)
        v = v - np.where(production, 2.5, 0.0)  # voltage drop under load
        voltages[f"V{i}"] = v

    currents = {}
    for i, factor in enumerate([1.0, 0.96, 1.05], start=1):
        currents[f"I{i}"] = s_kva * 1000 / (np.sqrt(3) * NOMINAL_VOLTAGE) * factor

    thd_v = np.where(production, rng.normal(4.2, 0.6, periods), rng.normal(2.4, 0.4, periods))
    thd_i = np.where(production, rng.normal(14.5, 2.0, periods), rng.normal(7.0, 1.5, periods))

    return pd.DataFrame(
        {
            "timestamp": ts,
            **{k: np.round(v, 2) for k, v in voltages.items()},
            **{k: np.round(v, 2) for k, v in currents.items()},
            "P_kW": np.round(p_kw, 2),
            "Q_kvar": np.round(q_kvar, 2),
            "S_kVA": np.round(s_kva, 2),
            "PF": np.round(pf, 3),
            "THD_V_pct": np.round(np.clip(thd_v, 0.5, None), 2),
            "THD_I_pct": np.round(np.clip(thd_i, 1.0, None), 2),
        }
    )


def main() -> None:
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "data/sample_measurements.csv")
    target.parent.mkdir(parents=True, exist_ok=True)
    generate().to_csv(target, index=False)
    print(f"Sample dataset written to {target}")


if __name__ == "__main__":
    main()
