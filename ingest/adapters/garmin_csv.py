#!/usr/bin/env python3
"""Garmin Connect CSV exports — and any other CSV you can describe in config.

CONTRACT = 1
DESCRIPTION = "Column-mapped CSVs (Garmin Connect by default)"

This adapter doesn't hard-code Garmin. It reads three CSVs whose columns are
named in `config.source`, so a different vendor's export is often a config
change rather than a new adapter: point `activity_columns` at their header
names, add their timestamp format, done.

Write a new adapter instead when the *shape* differs, not just the names —
one row per lap, a JSON or TCX export, sleep stages that need summing, several
files per week, distances in a column that also carries its unit.

Files are routed by substring on the filename (config.source.files), so
'Activities-2.csv' and 'activities (1).csv' both land as activities.
"""

from __future__ import annotations

from datetime import date

from ingest import Ingested
from ingest import parsers as p

CONTRACT = 1
DESCRIPTION = "Column-mapped CSVs (Garmin Connect by default)"

# config.source key holding each kind's column map, for error messages.
COLUMN_KEY = {"activities": "activity_columns", "sleep": "sleep_columns",
              "hrv": "hrv_columns"}


def hint(cfg):
    pat = cfg["source"]["files"]
    return ("Export activities, sleep and (optionally) HRV as CSV into "
            "data/raw/.\nFilenames are matched on the substrings "
            + ", ".join(repr(v) for v in pat.values())
            + " (configurable in config.json).")


def ingest(raw_dir, cfg, report):
    src = cfg["source"]
    missing = tuple(src["missing"])
    files = p.route_files(raw_dir, src["files"], report)

    out = Ingested()
    for path in files.get("activities", []):
        f = _open(path, src, missing)
        rows, note = _activities(f, src, report)
        out.sessions += rows
        report.file(f.name, "activities", len(f), len(rows), f.decimal, note)
    for path in files.get("sleep", []):
        f = _open(path, src, missing)
        rows, note = _sleep(f, src, report)
        out.sleep.update(rows)
        report.file(f.name, "sleep", len(f), len(rows), f.decimal, note)
    for path in files.get("hrv", []):
        f = _open(path, src, missing)
        rows, note = _hrv(f, src, report)
        out.hrv.update(rows)
        report.file(f.name, "hrv", len(f), len(rows), f.decimal, note)
    return out


def _open(path, src, missing):
    return p.CsvFile(path, decimal=src["decimal"], missing=missing)


def _date_column(col, header):
    """Configured date column, defaulting to the first one — which is where
    both the sleep and HRV exports put it."""
    return col.get("date") or (header[0] if header else None)


def _activities(f, src, report):
    col = src["activity_columns"]
    report.missing_columns("activities", f, col,
                           f"config.source.{COLUMN_KEY['activities']}")
    # Metres per unit of source.distance_unit -- only metre_distance_types needs it.
    per_metre = {"km": 1000.0, "mi": 1609.344}.get(src["distance_unit"], 1000.0)
    metres = [w.lower() for w in src["metre_distance_types"]]
    dur = tuple(src["duration_formats"]["activity"])

    out, bad = [], []
    for r in f.rows:
        raw_dt = f.text(r.get(col["date"]))
        if not raw_dt:
            continue
        dt = f.datetime(raw_dt, src["datetime_formats"])
        if dt is None:
            bad.append(raw_dt)
            continue
        atype = f.text(r.get(col["type"]))
        dist = f.num(r.get(col["distance"]))
        # Some activity types are exported in metres, the rest in distance_unit.
        if dist is not None and any(w in atype.lower() for w in metres):
            dist = dist / per_metre
        out.append({
            "datetime": dt.isoformat(sep=" "),
            "date": dt.date().isoformat(),
            "type": atype,
            "title": f.text(r.get(col["title"])),
            "distance_km": dist,
            "duration_s": f.seconds(r.get(col["duration"]), dur),
            "avg_hr": f.num(r.get(col["avg_hr"])),
            "max_hr": f.num(r.get(col["max_hr"])),
            "aerobic_te": f.num(r.get(col["aerobic_te"])),
            "cadence": f.num(r.get(col["cadence"])),
            "pace_s_per_km": f.seconds(r.get(col["pace"]), dur),
            "ascent_m": f.num(r.get(col["ascent"])),
            "stride_m": f.num(r.get(col["stride"])),
            "vert_osc_cm": f.num(r.get(col["vert_osc"])),
            "gct_ms": f.num(r.get(col["gct"])),
            "gct_left_pct": f.first_percent(r.get(col["gct_balance"])),
            "calories": f.num(r.get(col["calories"])),
        })
    report.skipped("activities", bad, "timestamp",
                   "add its format to config.source.datetime_formats")
    return out, ""


def _sleep(f, src, report):
    col = src["sleep_columns"]
    report.missing_columns("sleep", f, col,
                           f"config.source.{COLUMN_KEY['sleep']}")
    dcol = _date_column(col, f.header)
    slp = tuple(src["duration_formats"]["sleep"])

    out, bad = {}, []
    for r in f.rows:
        raw = f.text(r.get(dcol))
        if not raw:
            continue
        parsed = f.date(raw, src["date_formats"])
        if parsed is None:
            bad.append(raw)
            continue
        d = parsed.isoformat()
        dur = f.minutes(r.get(col["duration"]), slp)
        need = f.minutes(r.get(col["need"]), slp)
        out[d] = {
            "date": d,
            "sleep_score": f.num(r.get(col["score"])),
            "rhr": f.num(r.get(col["rhr"])),
            "body_battery": f.num(r.get(col["body_battery"])),
            "respiration": f.num(r.get(col["respiration"])),
            "hrv_7d": f.num(r.get(col["hrv_7d"])),
            "sleep_quality": f.text(r.get(col["quality"])),
            "sleep_min": dur,
            "sleep_need_min": need,
            "sleep_debt_min": (need - dur) if (dur and need) else None,
            "bedtime": f.text(r.get(col["bedtime"])),
            "waketime": f.text(r.get(col["waketime"])),
        }
    report.skipped("sleep", bad, "date",
                   "add its format to config.source.date_formats")
    return out, ""


def _hrv(f, src, report):
    """Dates matching date_formats are used as-is; year-less ones ('8 Aug')
    fall back to walking backwards from the newest row."""
    col = src["hrv_columns"]
    report.missing_columns("hrv", f, col, f"config.source.{COLUMN_KEY['hrv']}")
    dcol = _date_column(col, f.header)
    today = date.today()

    out, bad = {}, []
    cursor, inferred = None, 0
    for r in f.rows:
        raw = f.text(r.get(dcol))
        if not raw:
            continue
        d = f.date(raw, src["date_formats"])
        if d is None:
            d, cursor = p.infer_year_less_date(raw, cursor, today)
            if d is None:
                bad.append(raw)
                continue
            inferred += 1
        lo, hi = f.two_nums(r.get(col["baseline"]))
        out[d.isoformat()] = {
            "date": d.isoformat(),
            "hrv_night": f.strip_unit(r.get(col["overnight"])),
            "hrv_base_lo": lo,
            "hrv_base_hi": hi,
        }
    report.skipped("hrv", bad, "date",
                   "add its format to config.source.date_formats")
    return out, (f"{inferred} year-less dates inferred" if inferred else "")
