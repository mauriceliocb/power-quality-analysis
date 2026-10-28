# Power quality analysis and tariff study

Two Python scripts that work from the same power analyser export. The first one
produces the installation's power quality indicators, sizes the capacitor bank,
renders the charts and writes a report ready for review. The second one reuses
the measurement to compare Brazilian Group A tariff options and find the
cheapest contracted demand.

```bash
python src/power_quality.py data/sample_measurements.csv --output examples --tariff 0.62
```

```
672 records analysed.
Average power factor: 0.820
Suggested capacitor bank: 93 kvar
Report: examples/report.md
```

```bash
python src/tariff_study.py data/sample_measurements.csv --output examples
```

```
Green: BRL 41,092.10/month · Blue: BRL 39,638.94/month
Recommended tariff: Blue
Study: examples/tariff_study.md
```

Full outputs in [`examples/report.md`](examples/report.md) and
[`examples/tariff_study.md`](examples/tariff_study.md).

---

## The problem

In a power quality study of an industrial installation, the analyser returns one
record every 15 minutes: voltages, currents, power, power factor and harmonic
distortion. A single week of measurement is already hundreds of rows.

What came next was always the same work:

1. Open the dataset in a spreadsheet and check the columns.
2. Work out the indicators one by one: energy, peak demand, load factor, average
   power factor, voltage unbalance, THD.
3. Estimate the transformer K-factor.
4. Size the capacitor bank for the target power factor.
5. Build the charts.
6. Write the report and paste everything into it.

That is two to three hours of manual work per dataset. Worse, every step is a
chance to break a formula or drag a cell into the wrong place, and when a new
measurement arrives for the same client, it all starts over.

## The solution

The script automates everything from reading the CSV to the final report. What
took hours now takes seconds, and the same input always produces the same
output, regardless of who built the spreadsheet.

What it computes:

| Indicator | How it is derived |
| --- | --- |
| Active and reactive energy | Power integrated over the measurement step |
| Peak demand, average demand, load factor | Load curve statistics |
| Average power factor | Active energy over apparent energy for the period |
| Non-compliant hours | Time with power factor below 0.92 (PRODIST, Module 8) |
| Voltage unbalance | Maximum deviation from the three-phase average |
| Voltage and current THD | Period maximum and average |
| Transformer K-factor | Estimated from current THD with a typical non-linear load spectrum |
| Capacitor bank | kvar needed for the target power factor, sized on the heaviest load band |

Beyond the numbers, the report has a findings section that compares each
indicator against its reference limit and writes the alert only where there is
non-compliance.

## Tariff study

The same measurement answers a different question: is the installation on the
right tariff, with the right contracted demand? That is what the second script
does.

1. Splits energy and demand between the peak and off-peak windows defined in the
   tariff file.
2. Projects the measured period to a 30-day month.
3. For each tariff option, sweeps the possible contracted demands and keeps the
   cheapest total, accounting for the 5% tolerance and the overrun penalty.
4. Compares the Green and Blue tariffs, broken down into energy, demand and
   overrun, and states the annual saving of the better option.

Tariffs live in [`config/tariffs.json`](config/tariffs.json), outside the code.
Running the study for another client is a matter of replacing those values with
the ones in the utility's tariff resolution — no code changes.

In this repository's example, the Blue tariff comes out BRL 1,453.16 per month
cheaper, and the cheapest contracted demand lands at 254 kW, just below the
266 kW recorded peak, because the 5% tolerance absorbs the spike without
triggering a penalty. That is exactly the kind of adjustment a manual
calculation tends to miss.

### A note on Brazilian tariffs

Group A consumers (medium and high voltage) are billed for energy and for
contracted demand. The **Green** tariff has a single contracted demand and a much
higher energy price during the daily peak window; the **Blue** tariff has separate
contracted demands for peak and off-peak, with a smaller gap between energy
prices. Demand measured above 105% of the contracted value is charged at twice
the demand tariff.

## Usage

```bash
pip install -r requirements.txt

# generate a synthetic dataset to try it out
python src/generate_sample_data.py data/sample_measurements.csv

# run the power quality analysis
python src/power_quality.py data/sample_measurements.csv --output examples --target-pf 0.95 --tariff 0.62

# run the tariff study
python src/tariff_study.py data/sample_measurements.csv --output examples --tariffs config/tariffs.json
```

Arguments:

- `input` — CSV exported from the analyser.
- `--output` — folder where the report and charts are written.
- `--target-pf` — power factor to reach after correction (default: 0.95).
- `--tariff` — energy tariff in BRL/kWh, to estimate the cost of the period
  (optional).

Expected column names live in the `COLUMNS` dictionary at the top of
`power_quality.py`. To use a different analyser, map the names there: nothing
else in the code depends on the file format.

## Layout

```
src/power_quality.py          indicators, charts and the power quality report
src/tariff_study.py           Green vs Blue comparison and optimal contracted demand
src/generate_sample_data.py   synthetic dataset, so no client data is needed
config/tariffs.json           tariffs and peak window, editable without touching code
data/sample_measurements.csv  one week of measurement at 15-minute intervals
examples/                     generated output: reports and charts
```

## Design decisions

- **The capacitor bank is sized on the top decile of the load curve**, not on the
  period average. That is the critical condition: sizing on the average
  undercorrects production hours, which is exactly where the power factor drops.
- **The K-factor is a pre-sizing estimate.** The script assumes a typical
  three-phase non-linear load spectrum with amplitude proportional to 1/h. Final
  specification needs the measured spectrum.
- **Tariffs are configuration, not code.** They change every year and vary by
  utility; whoever updates the study should not have to write Python.
- **Contracted demand is chosen by search, not by rule of thumb.** Cost is not
  monotonic: contracting slightly below the peak can be cheaper, because the 5%
  tolerance absorbs the excess.
- **Reports are written in Markdown**, not PDF, so they can be reviewed,
  versioned and edited before becoming a final document.
- **Alerts are conditional.** A report that repeats "within limits" everywhere
  stops being read; only what needs action shows up.

## About real data

The dataset in this repository is synthetic. The scripts were written from a
real power quality study of an industrial installation, but no client data is
included here.

## Next steps

- Direct export to PDF and spreadsheet.
- Excess reactive energy charges under the applicable tariff.
- Free energy market evaluation, compared against the regulated scenario chosen
  in the tariff study.
- Tariff flags and seasonality in the tariff comparison.

## Stack

Python, pandas, NumPy and Matplotlib.
