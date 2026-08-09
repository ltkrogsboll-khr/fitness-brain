#!/usr/bin/env python3
"""
Ingest activity/sleep exports -> accumulate a local database -> emit a brief.

Why this exists: watch vendors cap Sleep/HRV exports at a rolling few weeks. This
script upserts every export into data/daily.csv and data/sessions.csv, so history
accumulates locally and that window stops mattering after a few months.

Reading the export is not this file's job. `ingest/` does that and hands back
sessions/sleep/hrv records in the shape ingest/schema.py defines; everything
here works on those, so a different data source is an adapter, not an edit.

Run:  python3 build.py
Input:  data/raw/*     -- read by the adapter named in config.source.adapter
Output: data/daily.csv, data/sessions.csv, context.md
"""

from __future__ import annotations

import csv
import math
import os
from datetime import date, datetime, timedelta

import config
import ingest
from ingest.parsers import read_csv

ROOT = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(ROOT, "data", "raw")
# Per-activity files (.fit), read at full resolution by analyze.py. Separate
# from RAW because these are one file per session rather than one export of
# everything, and because the adapter never sees them.
ACTIVITIES = os.path.join(ROOT, "data", "activities")
DAILY = os.path.join(ROOT, "data", "daily.csv")
SESSIONS = os.path.join(ROOT, "data", "sessions.csv")
CONTEXT = os.path.join(ROOT, "context.md")
JOURNAL = os.path.join(ROOT, "journal.md")

CFG = config.load()

# Written by earlier versions under sport-specific names. Dropped on rebuild so
# they don't linger in the CSVs as stale duplicates of the renamed field.
LEGACY_COLS = {"run_km", "avg_cadence", "impact_km"}

# Only for re-reading our own accumulated CSVs, where a blank cell is the
# normal way to say "no reading".
MISSING = set(CFG["source"]["missing"]) | {""}


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
        for r in read_csv(path)[1]:
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


def near_duplicates(sess, fit_rows, tolerance_s=900):
    """Sessions that look like the same workout counted twice.

    An activity file and a CSV export describing one run must produce the same
    (datetime, type) key or they become two sessions, and two sessions means
    double the TRIMP and double the distance — a wrong number that looks
    entirely plausible. Timestamps a few minutes apart are the tell: a vendor
    that logs when you pressed start and a file that logs first GPS fix will
    disagree by exactly that much. -> [(fit_row, other_row)]
    """
    out = []
    for r in fit_rows:
        try:
            t = datetime.fromisoformat(r["datetime"])
        except (KeyError, TypeError, ValueError):
            continue
        for s in sess:
            if (s.get("date") != r.get("date") or s.get("type") != r.get("type")
                    or s.get("datetime") == r.get("datetime")):
                continue
            try:
                t2 = datetime.fromisoformat(s["datetime"])
            except (TypeError, ValueError):
                continue
            if abs((t2 - t).total_seconds()) <= tolerance_s:
                out.append((r, s))
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

    # First run after a clone: make the drop-boxes rather than fail on them.
    os.makedirs(ACTIVITIES, exist_ok=True)
    if not os.path.isdir(RAW):
        os.makedirs(RAW, exist_ok=True)
        print(f"Created {os.path.relpath(RAW, ROOT)}/ — it was empty.\n"
              + ingest.hint(CFG))
        return

    rep = ingest.Report()
    try:
        got = ingest.run(RAW, CFG, rep)
    except ingest.AdapterError as e:
        print(f"Ingest failed: {e}")
        return
    acts, sleep, hrv = got.sessions, got.sleep, got.hrv

    # Activity files carry the same sessions at full resolution, plus the
    # derived fields the CSV can't express. They join `acts` here so they go
    # through the same TRIMP and upsert path — a .fit-only session is a real
    # session, not an annotation on one.
    import analyze
    fit_rows = analyze.activity_rows(CFG, rep)
    acts += [{k: v for k, v in r.items() if k != "_source_file"}
             for r in fit_rows]

    rep.print()
    if acts and not any(a["avg_hr"] for a in acts):
        print("  ! no activity has an average heart rate — TRIMP, and therefore "
              "every aerobic load figure, will be zero. Check what your adapter "
              "maps to avg_hr (python3 -m ingest --check)")

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

    for a, b in near_duplicates(sess, fit_rows):
        print(f"  ! {a['datetime']} {a['type']} (from an activity file) and "
              f"{b['datetime']} {b['type']} (already in sessions.csv) look "
              f"like the same workout counted twice.\n"
              f"    Its load is now doubled. Delete one row from "
              f"data/sessions.csv, and if this repeats, set "
              f"config.source.options.fit_sport_names so the types match.")

    # Before write_context, which reads journal.md back in: an activity file
    # dropped in the folder becomes a sentence the coach reads, with nobody
    # typing it. Idempotent — the line names its source file and is written once.
    for line in analyze.journal_for_rows(fit_rows, CFG):
        print(f"Journal  {line}")

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
        print("No usable rows. Drop your exports in data/raw/.\n"
              "If there are files there already, the Ingest lines above name "
              "what was skipped and which key would fix it; "
              "`python3 -m ingest --check` shows it field by field.")
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
    # ACWR stays on 7:28 whatever the cycle is; only the volume cap follows it.
    cyc = CFG["cycle"]["days"]

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
             f"Data through {end_d.isoformat()}. Planning cycle: {cyc} days. "
             f"Read `policy.md` before planning._\n")

    L.append("## Load\n")
    L.append("| Channel | Acute (7d) | Chronic (28d, per 7d) | ACWR | Ceiling |")
    L.append("|---|---|---|---|---|")
    L.append(f"| {aer['label']} ({aer['unit']}) | {ac_tr} | {ch_tr} | {acwr_tr} "
             f"| {aer['acwr_ceiling']} |")
    if mech["enabled"]:
        L.append(f"| {mech['label']} ({mech['unit']}) | {ac_im} | {ch_im} | "
                 f"{acwr_im} | {mech['acwr_ceiling']} |")
    if prim["enabled"]:
        cap = f" (cap {prim['cap']})" if prim["cap"] else ""
        L.append(f"\n{prim['label']} {prim['unit']} last {cyc}d: "
                 f"**{wsum(window(daily, end_d, cyc), 'primary_km')}**{cap}.")
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
