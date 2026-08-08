#!/usr/bin/env python3
"""
Ingest Garmin CSV exports -> accumulate a local database -> emit a coaching brief.

Why this exists: Garmin caps Sleep/HRV exports at a rolling 4 weeks. This script
upserts every export into data/daily.csv and data/sessions.csv, so history
accumulates locally and the 4-week window stops mattering after a few months.

Run:  python3 build.py
Input:  data/raw/*.csv   (Activities.csv, Sleep.csv, "HRV Status.csv")
Output: data/daily.csv, data/sessions.csv, context.md
"""

from __future__ import annotations

import csv
import math
import os
import re
from datetime import date, datetime, timedelta

ROOT = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(ROOT, "data", "raw")
DAILY = os.path.join(ROOT, "data", "daily.csv")
SESSIONS = os.path.join(ROOT, "data", "sessions.csv")
CONTEXT = os.path.join(ROOT, "context.md")
JOURNAL = os.path.join(ROOT, "journal.md")

# --- Athlete constants -------------------------------------------------------
# HR_MAX: no lab test, so we take the highest HR ever seen in the data and add a
# small margin. Override here if you know your true max.
HR_MAX_OVERRIDE = None
HR_REST_FALLBACK = 53

# Impact weighting: what each km of an activity costs the shins.
# Bone stress tracks impact, not aerobic effort -- cycling and swimming are free.
IMPACT_WEIGHT = {
    "Running": 1.0,
    "Trail Running": 1.15,
    "Treadmill Running": 0.85,  # softer deck
    "Walking": 0.25,
    "Hiking": 0.30,
}

MISSING = {"", "--", "---", None}


# --- Parsing helpers ---------------------------------------------------------
def num(v):
    """Parse Garmin's Danish-locale numbers: '6,56' -> 6.56, '7.032' -> 7032."""
    if v is None:
        return None
    s = str(v).strip().strip('"').lstrip("'")
    if s in MISSING:
        return None
    if "," in s and "." in s:  # '1.234,5' -> thousands dot, decimal comma
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    elif s.count(".") == 1 and len(s.split(".")[1]) == 3:
        s = s.replace(".", "")  # '7.032' steps -> 7032
    try:
        return float(s)
    except ValueError:
        return None


def dur_to_sec(v):
    """'00:44:47' or '00:05:13,7' or '6:50' -> seconds."""
    if v is None:
        return None
    s = str(v).strip().strip('"')
    if s in MISSING:
        return None
    s = s.replace(",", ".")
    parts = s.split(":")
    try:
        parts = [float(p) for p in parts]
    except ValueError:
        return None
    while len(parts) < 3:
        parts.insert(0, 0.0)
    return parts[0] * 3600 + parts[1] * 60 + parts[2]


def sleep_dur_to_min(v):
    """'6h 24min' -> 384."""
    if v is None or str(v).strip() in MISSING:
        return None
    m = re.match(r"(?:(\d+)h)?\s*(?:(\d+)min)?", str(v).strip())
    if not m:
        return None
    h = int(m.group(1) or 0)
    mi = int(m.group(2) or 0)
    return h * 60 + mi or None


def ms(v):
    """'57ms' -> 57.0"""
    if v is None or str(v).strip() in MISSING:
        return None
    return num(str(v).replace("ms", ""))


def gct_left(v):
    """'49,5% L / 50,5% R' -> 49.5"""
    if v is None or str(v).strip() in MISSING:
        return None
    m = re.search(r"([\d,\.]+)\s*%\s*L", str(v))
    return num(m.group(1)) if m else None


def strip_bom(s):
    return s.lstrip("﻿") if isinstance(s, str) else s


def read_csv(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        rdr = csv.DictReader(f)
        rdr.fieldnames = [strip_bom(c).strip() for c in (rdr.fieldnames or [])]
        return [dict(r) for r in rdr]


# --- Loaders -----------------------------------------------------------------
def load_activities(path):
    out = []
    for r in read_csv(path):
        raw_dt = (r.get("Date") or "").strip().strip('"')
        if not raw_dt:
            continue
        try:
            dt = datetime.strptime(raw_dt, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        atype = (r.get("Activity Type") or "").strip().strip('"')
        dist = num(r.get("Distance"))
        # Garmin exports swim distance in metres, everything else in km.
        if dist is not None and "Swim" in atype:
            dist = dist / 1000.0
        out.append(
            {
                "datetime": dt.isoformat(sep=" "),
                "date": dt.date().isoformat(),
                "type": atype,
                "title": (r.get("Title") or "").strip().strip('"'),
                "distance_km": dist,
                "duration_s": dur_to_sec(r.get("Time")),
                "avg_hr": num(r.get("Avg HR")),
                "max_hr": num(r.get("Max HR")),
                "aerobic_te": num(r.get("Aerobic TE")),
                "cadence": num(r.get("Avg Run Cadence")),
                "pace_s_per_km": dur_to_sec(r.get("Avg Pace")),
                "ascent_m": num(r.get("Total Ascent")),
                "stride_m": num(r.get("Avg Stride Length")),
                "vert_osc_cm": num(r.get("Avg Vertical Oscillation")),
                "gct_ms": num(r.get("Avg Ground Contact Time")),
                "gct_left_pct": gct_left(r.get("Avg GCT Balance")),
                "calories": num(r.get("Calories")),
            }
        )
    return out


def load_sleep(path):
    out = {}
    for r in read_csv(path):
        keys = list(r.keys())
        d = (r.get(keys[0]) or "").strip()
        if not re.match(r"\d{4}-\d{2}-\d{2}", d):
            continue
        dur = sleep_dur_to_min(r.get("Duration"))
        need = sleep_dur_to_min(r.get("Sleep Need"))
        out[d] = {
            "date": d,
            "sleep_score": num(r.get("Score")),
            "rhr": num(r.get("Resting Heart Rate")),
            "body_battery": num(r.get("Body Battery")),
            "respiration": num(r.get("Respiration")),
            "hrv_7d": num(r.get("HRV Status")),
            "sleep_quality": (r.get("Quality") or "").strip(),
            "sleep_min": dur,
            "sleep_need_min": need,
            "sleep_debt_min": (need - dur) if (dur and need) else None,
            "bedtime": (r.get("Bedtime") or "").strip(),
            "waketime": (r.get("Wake Time") or "").strip(),
        }
    return out


MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], start=1)}


def load_hrv(path, today):
    """HRV Status.csv has no year ('8 Aug'). Rows are consecutive descending days,
    so anchor the newest row to the most recent matching date <= today and walk back."""
    rows = read_csv(path)
    dated = []
    cursor = None
    for r in rows:
        keys = list(r.keys())
        raw = (r.get(keys[0]) or "").strip()
        m = re.match(r"(\d{1,2})\s+([A-Za-z]{3})", raw)
        if not m:
            continue
        day, mon = int(m.group(1)), MONTHS.get(m.group(2)[:3].title())
        if not mon:
            continue
        if cursor is None:
            year = today.year
            try:
                cand = date(year, mon, day)
            except ValueError:
                continue
            if cand > today:
                cand = date(year - 1, mon, day)
            cursor = cand
        else:
            cursor = cursor - timedelta(days=1)
            # self-heal if the file ever skips a day
            if cursor.day != day or cursor.month != mon:
                try:
                    cursor = date(cursor.year, mon, day)
                except ValueError:
                    pass
        lo = hi = None
        b = (r.get("Baseline") or "").strip()
        bm = re.match(r"([\d]+)\s*ms\s*-\s*([\d]+)\s*ms", b)
        if bm:
            lo, hi = float(bm.group(1)), float(bm.group(2))
        dated.append(
            {
                "date": cursor.isoformat(),
                "hrv_night": ms(r.get("Overnight HRV")),
                "hrv_base_lo": lo,
                "hrv_base_hi": hi,
            }
        )
    return {d["date"]: d for d in dated}


# --- Load model --------------------------------------------------------------
def trimp(duration_s, avg_hr, hr_rest, hr_max):
    """Banister TRIMP -- one uniform aerobic-load currency across all activity types."""
    if not duration_s or not avg_hr or not hr_max or hr_max <= hr_rest:
        return None
    hrr = (avg_hr - hr_rest) / (hr_max - hr_rest)
    hrr = max(0.0, min(1.0, hrr))
    return round((duration_s / 60.0) * hrr * 0.64 * math.exp(1.92 * hrr), 1)


def impact_km(a):
    w = IMPACT_WEIGHT.get(a["type"], 0.0)
    return round((a["distance_km"] or 0.0) * w, 2) if w else 0.0


# --- Upsert ------------------------------------------------------------------
def upsert(path, rows, key_fields):
    """Merge new rows into an accumulating CSV. Non-empty new values win."""
    existing = {}
    if os.path.exists(path):
        for r in read_csv(path):
            existing[tuple(r.get(k, "") for k in key_fields)] = r
    for r in rows:
        k = tuple(str(r.get(f, "") or "") for f in key_fields)
        if k in existing:
            merged = dict(existing[k])
            for f, v in r.items():
                if v not in (None, ""):
                    merged[f] = v
            existing[k] = merged
        else:
            existing[k] = {f: ("" if v is None else v) for f, v in r.items()}
    cols = []
    for r in existing.values():
        for c in r:
            if c not in cols:
                cols.append(c)
    out = sorted(existing.values(), key=lambda r: tuple(str(r.get(f, "")) for f in key_fields))
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in out:
            w.writerow({c: r.get(c, "") for c in cols})
    return out


def fnum(r, k):
    v = r.get(k)
    if v in MISSING:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# --- Main --------------------------------------------------------------------
def main():
    today = date.today()

    acts, sleep, hrv = [], {}, {}
    for fn in os.listdir(RAW):
        p = os.path.join(RAW, fn)
        low = fn.lower()
        if not low.endswith(".csv"):
            continue
        if "activities" in low:
            acts += load_activities(p)
        elif "sleep" in low:
            sleep.update(load_sleep(p))
        elif "hrv" in low:
            hrv.update(load_hrv(p, today))

    hr_max = HR_MAX_OVERRIDE or (
        max([a["max_hr"] for a in acts if a["max_hr"]] or [180]) + 5
    )
    rhrs = [v["rhr"] for v in sleep.values() if v["rhr"]]
    hr_rest = round(sum(rhrs) / len(rhrs)) if rhrs else HR_REST_FALLBACK

    for a in acts:
        a["trimp"] = trimp(a["duration_s"], a["avg_hr"], hr_rest, hr_max)
        a["impact_km"] = impact_km(a)

    sess = upsert(SESSIONS, acts, ["datetime", "type"])

    # Daily spine: union of every date we have any signal for.
    by_day = {}
    for r in sess:
        d = r.get("date")
        if not d:
            continue
        e = by_day.setdefault(d, {"trimp": 0.0, "impact_km": 0.0, "run_km": 0.0,
                                  "types": [], "cad": [], "n": 0})
        e["trimp"] += fnum(r, "trimp") or 0.0
        e["impact_km"] += fnum(r, "impact_km") or 0.0
        if "Running" in (r.get("type") or ""):
            e["run_km"] += fnum(r, "distance_km") or 0.0
            c = fnum(r, "cadence")
            if c:
                e["cad"].append(c)
        e["types"].append(r.get("type"))
        e["n"] += 1

    all_dates = sorted(set(list(sleep.keys()) + list(hrv.keys()) + list(by_day.keys())))
    if not all_dates:
        print("No data found in data/raw/. Drop the Garmin CSVs there first.")
        return

    start, end = date.fromisoformat(all_dates[0]), date.fromisoformat(all_dates[-1])
    daily = []
    d = start
    while d <= end:
        k = d.isoformat()
        t = by_day.get(k, {})
        s = sleep.get(k, {})
        h = hrv.get(k, {})
        cad = t.get("cad") or []
        daily.append({
            "date": k,
            "dow": d.strftime("%a"),
            "trimp": round(t.get("trimp", 0.0), 1),
            "impact_km": round(t.get("impact_km", 0.0), 2),
            "run_km": round(t.get("run_km", 0.0), 2),
            "sessions": t.get("n", 0),
            "activities": ", ".join([x for x in t.get("types", []) if x]),
            "avg_cadence": round(sum(cad) / len(cad), 1) if cad else None,
            "sleep_score": s.get("sleep_score"),
            "sleep_min": s.get("sleep_min"),
            "sleep_debt_min": s.get("sleep_debt_min"),
            "rhr": s.get("rhr"),
            "body_battery": s.get("body_battery"),
            "respiration": s.get("respiration"),
            "hrv_7d": s.get("hrv_7d") or h.get("hrv_7d"),
            "hrv_night": h.get("hrv_night"),
            "hrv_base_lo": h.get("hrv_base_lo"),
            "hrv_base_hi": h.get("hrv_base_hi"),
        })
        d += timedelta(days=1)

    daily = upsert(DAILY, daily, ["date"])
    daily.sort(key=lambda r: r["date"])
    write_context(daily, sess, hr_rest, hr_max, today)
    print(f"OK  sessions={len(sess)}  days={len(daily)}  "
          f"span={daily[0]['date']}..{daily[-1]['date']}  "
          f"hr_rest={hr_rest} hr_max={hr_max}")
    print(f"    -> {DAILY}\n    -> {SESSIONS}\n    -> {CONTEXT}")


def window(daily, end_d, days):
    lo = end_d - timedelta(days=days - 1)
    return [r for r in daily
            if lo <= date.fromisoformat(r["date"]) <= end_d]


def wsum(rows, key):
    return round(sum(fnum(r, key) or 0.0 for r in rows), 1)


def wavg(rows, key):
    vals = [fnum(r, key) for r in rows if fnum(r, key) is not None]
    return round(sum(vals) / len(vals), 1) if vals else None


def write_context(daily, sess, hr_rest, hr_max, today):
    end_d = date.fromisoformat(daily[-1]["date"])
    a7, c28 = window(daily, end_d, 7), window(daily, end_d, 28)

    ac_tr, ch_tr = wsum(a7, "trimp"), round(wsum(c28, "trimp") / 4.0, 1)
    ac_im, ch_im = wsum(a7, "impact_km"), round(wsum(c28, "impact_km") / 4.0, 2)
    acwr_tr = round(ac_tr / ch_tr, 2) if ch_tr else None
    acwr_im = round(ac_im / ch_im, 2) if ch_im else None

    # Today's row is usually empty until the watch syncs -- report the most
    # recent night that actually has data, and say which night it was.
    slept = [r for r in daily if fnum(r, "sleep_score")]
    last = slept[-1] if slept else daily[-1]
    recent = [r for r in daily[-10:] if fnum(r, "hrv_night")]
    hrv_n = fnum(recent[-1], "hrv_night") if recent else None
    lo = fnum(recent[-1], "hrv_base_lo") if recent else None
    hi = fnum(recent[-1], "hrv_base_hi") if recent else None
    rhr_recent = [fnum(r, "rhr") for r in window(daily, end_d, 3) if fnum(r, "rhr")]
    rhr_base = wavg(c28, "rhr")
    debt3 = round(sum(fnum(r, "sleep_debt_min") or 0 for r in window(daily, end_d, 3)) / 60.0, 1)

    flags = []
    if hrv_n and lo and hrv_n < lo:
        flags.append(f"HRV {hrv_n:.0f}ms is BELOW baseline ({lo:.0f}-{hi:.0f}ms)")
    if rhr_recent and rhr_base and (sum(rhr_recent) / len(rhr_recent)) >= rhr_base + 3:
        flags.append(f"RHR {sum(rhr_recent)/len(rhr_recent):.0f} vs {rhr_base:.0f} baseline (+3 or more)")
    if debt3 > 2:
        flags.append(f"Sleep debt {debt3:.1f}h over 3 days")
    if acwr_im and acwr_im > 1.3:
        flags.append(f"Impact ACWR {acwr_im} above 1.3 ceiling")
    if acwr_tr and acwr_tr > 1.5:
        flags.append(f"Aerobic ACWR {acwr_tr} above 1.5")

    runs = [s for s in sess if "Running" in (s.get("type") or "")]
    runs.sort(key=lambda s: s.get("datetime", ""))
    cad_recent = [fnum(s, "cadence") for s in runs[-5:] if fnum(s, "cadence")]

    L = []
    L.append("# Coaching context\n")
    L.append(f"_Generated {datetime.now():%Y-%m-%d %H:%M} from data/raw. "
             f"Data through {end_d.isoformat()}. Read `policy.md` before planning._\n")

    L.append("## Load — two channels\n")
    L.append("| Channel | Acute (7d) | Chronic (28d avg wk) | ACWR | Ceiling |")
    L.append("|---|---|---|---|---|")
    L.append(f"| Aerobic (TRIMP) | {ac_tr} | {ch_tr} | {acwr_tr} | 1.5 |")
    L.append(f"| Impact (weighted km) | {ac_im} | {ch_im} | {acwr_im} | 1.3 |")
    L.append(f"\nRunning km last 7d: **{wsum(a7, 'run_km')}**. "
             f"Constants: HR_rest={hr_rest}, HR_max={hr_max}.\n")

    L.append("## Readiness\n")
    L.append(f"- Night of {last.get('date')}: sleep score {last.get('sleep_score') or '--'}, "
             f"{last.get('sleep_min') or '--'} min, RHR {last.get('rhr') or '--'}, "
             f"Body Battery {last.get('body_battery') or '--'}")
    L.append(f"- HRV overnight {hrv_n or '--'} ms, baseline "
             f"{lo:.0f}-{hi:.0f} ms" if lo else "- HRV baseline unavailable")
    L.append(f"- 7d avg HRV: {wavg(a7, 'hrv_7d')} ms | 3d sleep debt: {debt3} h")
    if cad_recent:
        L.append(f"- Cadence, last {len(cad_recent)} runs: "
                 f"{', '.join(f'{c:.0f}' for c in cad_recent)} spm "
                 f"(target per policy: 168+)")
    L.append("")

    L.append("## Flags\n")
    L.extend([f"- {f}" for f in flags] or ["- None."])
    L.append("")

    L.append("## Last 14 days\n")
    L.append("| Date | Day | Activities | Run km | TRIMP | Sleep | RHR | HRV | BB |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for r in daily[-14:]:
        L.append("| {date} | {dow} | {act} | {rk} | {tr} | {ss} | {rhr} | {hv} | {bb} |".format(
            date=r["date"], dow=r["dow"],
            act=(r.get("activities") or "-")[:34] or "-",
            rk=r.get("run_km") or "-", tr=r.get("trimp") or "-",
            ss=r.get("sleep_score") or "-", rhr=r.get("rhr") or "-",
            hv=r.get("hrv_night") or r.get("hrv_7d") or "-",
            bb=r.get("body_battery") or "-"))
    L.append("")

    L.append("## Recent sessions\n")
    L.append("| Date | Type | km | Time | Avg HR | TE | Cad | Ascent |")
    L.append("|---|---|---|---|---|---|---|---|")
    for s in sorted(sess, key=lambda x: x.get("datetime", ""))[-12:]:
        secs = fnum(s, "duration_s") or 0
        L.append("| {d} | {t} | {km} | {mm} | {hr} | {te} | {cad} | {asc} |".format(
            d=(s.get("datetime") or "")[:16], t=s.get("type"),
            km=s.get("distance_km") or "-", mm=f"{int(secs//60)}:{int(secs%60):02d}",
            hr=s.get("avg_hr") or "-", te=s.get("aerobic_te") or "-",
            cad=s.get("cadence") or "-", asc=s.get("ascent_m") or "-"))
    L.append("")

    if os.path.exists(JOURNAL):
        with open(JOURNAL, encoding="utf-8") as f:
            entries = [ln for ln in f.read().splitlines() if ln.startswith("20")]
        L.append("## Journal (latest 10)\n")
        L.extend([f"- {e}" for e in entries[-10:]] or ["- empty"])
        L.append("")

    with open(CONTEXT, "w", encoding="utf-8") as f:
        f.write("\n".join(L))


if __name__ == "__main__":
    main()
