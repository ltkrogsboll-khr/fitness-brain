#!/usr/bin/env python3
"""
Ingest activity/sleep CSV exports -> accumulate a local database -> emit a brief.

Why this exists: watch vendors cap Sleep/HRV exports at a rolling few weeks. This
script upserts every export into data/daily.csv and data/sessions.csv, so history
accumulates locally and that window stops mattering after a few months.

Run:  python3 build.py
Input:  data/raw/*.csv   -- routed by filename, columns named in config.json
Output: data/daily.csv, data/sessions.csv, context.md
"""

from __future__ import annotations

import csv
import math
import os
import re
from datetime import date, datetime, timedelta

import config

ROOT = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(ROOT, "data", "raw")
DAILY = os.path.join(ROOT, "data", "daily.csv")
SESSIONS = os.path.join(ROOT, "data", "sessions.csv")
CONTEXT = os.path.join(ROOT, "context.md")
JOURNAL = os.path.join(ROOT, "journal.md")

CFG = config.load()

# Written by earlier versions under sport-specific names. Dropped on rebuild so
# they don't linger in the CSVs as stale duplicates of the renamed field.
LEGACY_COLS = {"run_km", "avg_cadence", "impact_km"}

MISSING = {"", "--", "---", None}


# --- Parsing helpers ---------------------------------------------------------
def num(v):
    """Parse comma-decimal locales: '6,56' -> 6.56, '7.032' -> 7032."""
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
    col = CFG["source"]["activity_columns"]
    metres = CFG["source"]["metre_distance_types"]
    out = []
    for r in read_csv(path):
        raw_dt = (r.get(col["date"]) or "").strip().strip('"')
        if not raw_dt:
            continue
        try:
            dt = datetime.strptime(raw_dt, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        atype = (r.get(col["type"]) or "").strip().strip('"')
        dist = num(r.get(col["distance"]))
        # Some activity types are exported in metres, everything else in km.
        if dist is not None and any(w in atype for w in metres):
            dist = dist / 1000.0
        out.append(
            {
                "datetime": dt.isoformat(sep=" "),
                "date": dt.date().isoformat(),
                "type": atype,
                "title": (r.get(col["title"]) or "").strip().strip('"'),
                "distance_km": dist,
                "duration_s": dur_to_sec(r.get(col["duration"])),
                "avg_hr": num(r.get(col["avg_hr"])),
                "max_hr": num(r.get(col["max_hr"])),
                "aerobic_te": num(r.get(col["aerobic_te"])),
                "cadence": num(r.get(col["cadence"])),
                "pace_s_per_km": dur_to_sec(r.get(col["pace"])),
                "ascent_m": num(r.get(col["ascent"])),
                "stride_m": num(r.get(col["stride"])),
                "vert_osc_cm": num(r.get(col["vert_osc"])),
                "gct_ms": num(r.get(col["gct"])),
                "gct_left_pct": gct_left(r.get(col["gct_balance"])),
                "calories": num(r.get(col["calories"])),
            }
        )
    return out


def load_sleep(path):
    col = CFG["source"]["sleep_columns"]
    out = {}
    for r in read_csv(path):
        keys = list(r.keys())
        d = (r.get(keys[0]) or "").strip()
        if not re.match(r"\d{4}-\d{2}-\d{2}", d):
            continue
        dur = sleep_dur_to_min(r.get(col["duration"]))
        need = sleep_dur_to_min(r.get(col["need"]))
        out[d] = {
            "date": d,
            "sleep_score": num(r.get(col["score"])),
            "rhr": num(r.get(col["rhr"])),
            "body_battery": num(r.get(col["body_battery"])),
            "respiration": num(r.get(col["respiration"])),
            "hrv_7d": num(r.get(col["hrv_7d"])),
            "sleep_quality": (r.get(col["quality"]) or "").strip(),
            "sleep_min": dur,
            "sleep_need_min": need,
            "sleep_debt_min": (need - dur) if (dur and need) else None,
            "bedtime": (r.get(col["bedtime"]) or "").strip(),
            "waketime": (r.get(col["waketime"]) or "").strip(),
        }
    return out


MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], start=1)}


def load_hrv(path, today):
    """The HRV export carries no year ('8 Aug'). Rows are consecutive descending
    days, so anchor the newest row to the most recent matching date <= today and
    walk back."""
    col = CFG["source"]["hrv_columns"]
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
        b = (r.get(col["baseline"]) or "").strip()
        bm = re.match(r"([\d]+)\s*ms\s*-\s*([\d]+)\s*ms", b)
        if bm:
            lo, hi = float(bm.group(1)), float(bm.group(2))
        dated.append(
            {
                "date": cursor.isoformat(),
                "hrv_night": ms(r.get(col["overnight"])),
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


def mech_km(a):
    """Weighted distance for the second load channel -- 0 when it's disabled."""
    w = CFG["mechanical"]["weights"].get(a["type"], 0.0)
    return round((a["distance_km"] or 0.0) * w, 2) if w else 0.0


def is_primary(atype):
    """Does this activity type count toward headline volume?"""
    m = CFG["primary"]["match"]
    return bool(m) and any(w.lower() in (atype or "").lower() for w in m)


# --- Upsert ------------------------------------------------------------------
def upsert(path, rows, key_fields, drop=()):
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
            if c not in cols and c not in drop:
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
        pat = CFG["source"]["files"]
        if pat["activities"] in low:
            acts += load_activities(p)
        elif pat["sleep"] in low:
            sleep.update(load_sleep(p))
        elif pat["hrv"] in low:
            hrv.update(load_hrv(p, today))

    hr_max = CFG["athlete"]["hr_max_override"] or (
        max([a["max_hr"] for a in acts if a["max_hr"]] or [180]) + 5
    )
    rhrs = [v["rhr"] for v in sleep.values() if v["rhr"]]
    hr_rest = round(sum(rhrs) / len(rhrs)) if rhrs \
        else CFG["athlete"]["hr_rest_fallback"]

    for a in acts:
        a["trimp"] = trimp(a["duration_s"], a["avg_hr"], hr_rest, hr_max)
        a["mech_km"] = mech_km(a)

    sess = upsert(SESSIONS, acts, ["datetime", "type"], drop=LEGACY_COLS)

    fm = CFG["form_metric"]

    # Daily spine: union of every date we have any signal for.
    by_day = {}
    for r in sess:
        d = r.get("date")
        if not d:
            continue
        e = by_day.setdefault(d, {"trimp": 0.0, "mech_km": 0.0, "primary_km": 0.0,
                                  "types": [], "fm": [], "n": 0})
        e["trimp"] += fnum(r, "trimp") or 0.0
        e["mech_km"] += fnum(r, "mech_km") or 0.0
        if is_primary(r.get("type")):
            e["primary_km"] += fnum(r, "distance_km") or 0.0
            v = fnum(r, fm["field"]) if fm["enabled"] else None
            if v:
                e["fm"].append(v)
        e["types"].append(r.get("type"))
        e["n"] += 1

    all_dates = sorted(set(list(sleep.keys()) + list(hrv.keys()) + list(by_day.keys())))
    if not all_dates:
        print("No data found in data/raw/. Drop your CSV exports there first.")
        return

    start, end = date.fromisoformat(all_dates[0]), date.fromisoformat(all_dates[-1])
    daily = []
    d = start
    while d <= end:
        k = d.isoformat()
        t = by_day.get(k, {})
        s = sleep.get(k, {})
        h = hrv.get(k, {})
        fmv = t.get("fm") or []
        daily.append({
            "date": k,
            "dow": d.strftime("%a"),
            "trimp": round(t.get("trimp", 0.0), 1),
            "mech_km": round(t.get("mech_km", 0.0), 2),
            "primary_km": round(t.get("primary_km", 0.0), 2),
            "sessions": t.get("n", 0),
            "activities": ", ".join([x for x in t.get("types", []) if x]),
            "avg_form": round(sum(fmv) / len(fmv), 1) if fmv else None,
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

    daily = upsert(DAILY, daily, ["date"], drop=LEGACY_COLS)
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

    aer, mech, prim, fm, rd = (CFG["aerobic"], CFG["mechanical"],
                               CFG["primary"], CFG["form_metric"],
                               CFG["readiness"])

    ac_tr, ch_tr = wsum(a7, "trimp"), round(wsum(c28, "trimp") / 4.0, 1)
    ac_im, ch_im = wsum(a7, "mech_km"), round(wsum(c28, "mech_km") / 4.0, 2)
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

    over = rd["rhr_above_baseline"]
    flags = []
    if hrv_n and lo and hrv_n < lo:
        flags.append(f"HRV {hrv_n:.0f}ms is BELOW baseline ({lo:.0f}-{hi:.0f}ms)")
    if rhr_recent and rhr_base and (sum(rhr_recent) / len(rhr_recent)) >= rhr_base + over:
        flags.append(f"RHR {sum(rhr_recent)/len(rhr_recent):.0f} vs {rhr_base:.0f} "
                     f"baseline (+{over} or more)")
    if debt3 > rd["sleep_debt_h"]:
        flags.append(f"Sleep debt {debt3:.1f}h over 3 days")
    if mech["enabled"] and acwr_im and acwr_im > mech["acwr_ceiling"]:
        flags.append(f"{mech['label']} ACWR {acwr_im} above "
                     f"{mech['acwr_ceiling']} ceiling")
    if acwr_tr and acwr_tr > aer["acwr_ceiling"]:
        flags.append(f"{aer['label']} ACWR {acwr_tr} above {aer['acwr_ceiling']}")

    prim_sess = [s for s in sess if is_primary(s.get("type"))]
    prim_sess.sort(key=lambda s: s.get("datetime", ""))
    fm_recent = ([fnum(s, fm["field"]) for s in prim_sess[-5:] if fnum(s, fm["field"])]
                 if fm["enabled"] else [])

    L = []
    L.append("# Coaching context\n")
    L.append(f"_Generated {datetime.now():%Y-%m-%d %H:%M} from data/raw. "
             f"Data through {end_d.isoformat()}. Read `policy.md` before planning._\n")

    L.append("## Load\n")
    L.append("| Channel | Acute (7d) | Chronic (28d avg wk) | ACWR | Ceiling |")
    L.append("|---|---|---|---|---|")
    L.append(f"| {aer['label']} ({aer['unit']}) | {ac_tr} | {ch_tr} | {acwr_tr} "
             f"| {aer['acwr_ceiling']} |")
    if mech["enabled"]:
        L.append(f"| {mech['label']} ({mech['unit']}) | {ac_im} | {ch_im} | "
                 f"{acwr_im} | {mech['acwr_ceiling']} |")
    if prim["enabled"]:
        L.append(f"\n{prim['label']} {prim['unit']} last 7d: "
                 f"**{wsum(a7, 'primary_km')}**.")
    L.append(f"\nConstants: HR_rest={hr_rest}, HR_max={hr_max}.\n")

    L.append("## Readiness\n")
    L.append(f"- Night of {last.get('date')}: sleep score {last.get('sleep_score') or '--'}, "
             f"{last.get('sleep_min') or '--'} min, RHR {last.get('rhr') or '--'}, "
             f"Body Battery {last.get('body_battery') or '--'}")
    L.append(f"- HRV overnight {hrv_n or '--'} ms, baseline "
             f"{lo:.0f}-{hi:.0f} ms" if lo else "- HRV baseline unavailable")
    L.append(f"- 7d avg HRV: {wavg(a7, 'hrv_7d')} ms | 3d sleep debt: {debt3} h")
    if fm_recent:
        tgt = f" (target per policy: {fm['target']}+)" if fm["target"] else ""
        L.append(f"- {fm['label']}, last {len(fm_recent)} sessions: "
                 f"{', '.join(f'{c:.0f}' for c in fm_recent)} {fm['unit']}{tgt}")
    L.append("")

    L.append("## Flags\n")
    L.extend([f"- {f}" for f in flags] or ["- None."])
    L.append("")

    L.append("## Last 14 days\n")
    L.append(f"| Date | Day | Activities | {prim['unit']} | {aer['unit']} "
             f"| Sleep | RHR | HRV | BB |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for r in daily[-14:]:
        L.append("| {date} | {dow} | {act} | {rk} | {tr} | {ss} | {rhr} | {hv} | {bb} |".format(
            date=r["date"], dow=r["dow"],
            act=(r.get("activities") or "-")[:34] or "-",
            rk=r.get("primary_km") or "-", tr=r.get("trimp") or "-",
            ss=r.get("sleep_score") or "-", rhr=r.get("rhr") or "-",
            hv=r.get("hrv_night") or r.get("hrv_7d") or "-",
            bb=r.get("body_battery") or "-"))
    L.append("")

    L.append("## Recent sessions\n")
    fm_col = fm["label"] if fm["enabled"] else "—"
    L.append(f"| Date | Type | km | Time | Avg HR | TE | {fm_col} | Ascent |")
    L.append("|---|---|---|---|---|---|---|---|")
    for s in sorted(sess, key=lambda x: x.get("datetime", ""))[-12:]:
        secs = fnum(s, "duration_s") or 0
        L.append("| {d} | {t} | {km} | {mm} | {hr} | {te} | {fmv} | {asc} |".format(
            d=(s.get("datetime") or "")[:16], t=s.get("type"),
            km=s.get("distance_km") or "-", mm=f"{int(secs//60)}:{int(secs%60):02d}",
            hr=s.get("avg_hr") or "-", te=s.get("aerobic_te") or "-",
            fmv=(s.get(fm["field"]) if fm["enabled"] else None) or "-",
            asc=s.get("ascent_m") or "-"))
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
