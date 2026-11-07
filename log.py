#!/usr/bin/env python3
"""Log hand-measured values into data/measurements.csv.

    python3 log.py weight 82.4 waist 91
    python3 log.py ldl 5.5 --date 2026-11-05 --source gp
    python3 log.py --show weight        # last 14 readings

Metrics, units and plausible ranges come from data/metrics.csv (add a row there
to start tracking something new). One row per (date, metric, source): logging
the same metric twice on a day replaces the earlier value.
"""
import argparse
import csv
import sys
from datetime import date
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data"
MEAS = DATA / "measurements.csv"
REG = DATA / "metrics.csv"
FIELDS = ["date", "metric", "value", "unit", "source", "note"]


def registry():
    return {r["metric"]: r for r in csv.DictReader(open(REG))}


def load():
    return list(csv.DictReader(open(MEAS))) if MEAS.exists() else []


def save(rows):
    rows.sort(key=lambda r: (r["date"], r["metric"]))
    tmp = MEAS.with_suffix(".tmp")
    with open(tmp, "w", newline="") as f:
        w = csv.DictWriter(f, FIELDS)
        w.writeheader()
        w.writerows(rows)
    tmp.replace(MEAS)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pairs", nargs="*", help="metric value [metric value ...]")
    ap.add_argument("--date", default=date.today().isoformat())
    ap.add_argument("--source", default="manual")
    ap.add_argument("--note", default="")
    ap.add_argument("--show", metavar="METRIC")
    a = ap.parse_args()
    reg = registry()
    rows = load()

    if a.show:
        for r in [r for r in rows if r["metric"] == a.show][-14:]:
            print(r["date"], r["value"], r["unit"], r["note"])
        return
    if not a.pairs or len(a.pairs) % 2:
        sys.exit("give metric/value pairs, e.g. weight 82.4 waist 91")
    date.fromisoformat(a.date)

    new = []
    for m, v in zip(a.pairs[::2], a.pairs[1::2]):
        if m not in reg or reg[m]["store"] != "measurements":
            sys.exit(f"unknown metric '{m}'. Loggable: {', '.join(k for k, r in reg.items() if r['store'] == 'measurements')}")
        x = float(v.replace(",", "."))
        lo, hi = float(reg[m]["min"]), float(reg[m]["max"])
        if not lo <= x <= hi:
            sys.exit(f"{m} {x} outside plausible range {lo}-{hi} {reg[m]['unit']}")
        new.append({"date": a.date, "metric": m, "value": f"{x:g}", "unit": reg[m]["unit"], "source": a.source, "note": a.note})

    keys = {(r["date"], r["metric"], r["source"]) for r in new}
    rows = [r for r in rows if (r["date"], r["metric"], r["source"]) not in keys] + new
    save(rows)
    for r in new:
        print(f"{r['date']}  {r['metric']} {r['value']} {r['unit']}")


if __name__ == "__main__":
    main()
