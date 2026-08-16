#!/usr/bin/env python3
"""Close analysis of one activity, from the file your watch recorded for it.

    (drop a file in data/activities/, then just rebuild — see below)

    python3 analyze.py                          # newest in data/activities/
    python3 analyze.py --all                    # every file there, once each
    python3 analyze.py ~/Downloads/1234_ACTIVITY.fit
    python3 analyze.py run.fit --hr-cap 140 --cadence 170
    python3 analyze.py run.fit --dry-run        # print, write nothing

Drop activity files in `data/activities/`. That folder is named after what the
files are, not who made them: Garmin, Wahoo, Coros and Suunto all write .fit,
while other platforms export .tcx or .gpx for the same run. Which formats work
is a question of which readers exist in `ingest/readers/` — they are claimed by
file extension, so a folder holding two formats needs no configuration.
`python3 -m ingest --readers` lists them; `ingest/activity.py` is the contract
for writing another.

You do not have to run this by hand. `build.py` reads the same folder on every
rebuild, so dropping a file and pressing Rebuild in the dashboard is the whole
loop. This script exists for the targeted case: re-reading one session against a
particular target (`--hr-cap`), or looking before you write.

Why it exists at all: the CSV export gives one average per session, and an
average over a run that ends in a walk home describes neither the run nor the
walk. "Cadence 154" and "cadence 171 for sixteen minutes, then a walk" are the
same CSV row and completely different training.

What it does with what it finds:

  numbers -> data/sessions.csv, as extra fields on the session row. The schema
             keeps unknown session fields (ingest/schema.py), so most of them
             ride through into context.md and the dashboard with no engine
             change, and are nameable as config.form_metric.field — so
             `moving_cadence` can become the tracked number instead of the
             walk-diluted `cadence` the CSV supplies. Three are the exception:
             build.py's trimp() and mech_km() prefer moving_time_s /
             moving_avg_hr / moving_distance_km over the vendor's
             whole-activity totals when a row has them, so a walked warm-up
             or cooldown doesn't inflate TRIMP or mechanical load either.
  prose   -> one line in journal.md, which build.py folds into context.md and
             serve.py injects into every coach conversation.

So a file dropped in the folder reaches the next planning conversation by
itself. Neither output stores the per-second streams: nothing downstream can
consume 1 Hz data, and the file is still on disk when a new question needs
asking of it.
"""

from __future__ import annotations

import argparse
import os
import sys

import config
from ingest import activity

ROOT = os.path.dirname(os.path.abspath(__file__))
ACTIVITIES = os.path.join(ROOT, "data", "activities")
CFG = config.load()


def readable():
    """Extensions some reader in ingest/readers/ claims. Asked fresh each time
    rather than frozen in a constant, so dropping a reader into
    ingest/readers/local/ starts working without touching this file."""
    return activity.extensions()

# Fields this script owns, named so they can't collide with the vendor's own
# averages: `cadence` is what the CSV says, `moving_cadence` is what you did.
DERIVED = ["moving_time_s", "moving_avg_hr", "moving_cadence",
           "moving_pace_s_per_km", "moving_distance_km", "hr_cap_used",
           "pct_above_hr_cap", "cadence_in_band_pct", "hr_drift_bpm",
           "decoupling_pct", "walk_break_s"]


def settings(cfg=None, hr_cap=None, cadence=None, band=None, floor=None):
    """The four numbers the analysis measures adherence against.

    They describe what the *current plan* asked for, so they come from config
    and are overridable per run. Explicit arguments win; None means "use
    config", which is what build.py always does.
    """
    cfg = cfg or CFG
    an = cfg.get("analysis", {}) or {}
    return {
        "hr_cap": hr_cap if hr_cap is not None else an.get("hr_cap"),
        "cadence": cadence if cadence is not None else (
            an.get("cadence_target") or cfg["form_metric"].get("target")),
        "band": band if band is not None else an.get("cadence_band", 5),
        "floor": floor if floor is not None else an.get("walk_cadence_floor", 140),
    }


# --- Deriving ----------------------------------------------------------------
def moving_mask(act, floor):
    """Which records count as 'actually doing the session'.

    For a run that's a cadence threshold, because the thing being excluded is
    walking — which has a perfectly good cadence and a perfectly good speed, and
    is the reason the session average lies. For everything else it's simply
    whether you were moving: a cyclist freewheeling downhill is still riding,
    and a walk is the entire point of a walk.
    """
    out = []
    for r in act.records:
        if r.get("t") is None:
            continue
        if act.is_run:
            c = r.get("cadence")
            out.append((r, c is not None and c >= floor))
        else:
            s = r.get("speed")
            out.append((r, s is not None and s > 0.5))
    return out


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def analyse(act, hr_cap=None, cadence=None, band=5, floor=140):
    """-> dict of conclusions. Every value is None when unmeasurable."""
    mask = moving_mask(act, floor)
    moving = [r for r, m in mask if m]
    if not moving:
        return {}

    # Sample spacing, not a sample count: several formats drop to smart
    # recording when nothing changes, so "1230 records" isn't "1230 seconds".
    span = [r["t"] for r in act.records if r.get("t") is not None]
    dt = ((max(span) - min(span)) / (len(span) - 1)) if len(span) > 1 else 1.0

    hrs = [r.get("heart_rate") for r in moving]
    cads = [r.get("cadence") for r in moving]
    spds = [r.get("speed") for r in moving]

    moving_time = len(moving) * dt
    out = {"moving_time_s": round(moving_time)}
    mh, mc, ms = _mean(hrs), _mean(cads), _mean(spds)
    out["moving_avg_hr"] = round(mh) if mh else None
    out["moving_cadence"] = round(mc) if mc else None
    out["moving_pace_s_per_km"] = round(1000.0 / ms) if ms else None
    # Same mask as moving_time_s/moving_avg_hr, so a walk warm-up doesn't
    # inflate the distance that feeds mech_km any more than it inflates HR.
    out["moving_distance_km"] = round(moving_time * ms / 1000.0, 2) if ms else None

    # Adherence to the two things the plan actually asked for.
    if hr_cap:
        have = [h for h in hrs if h is not None]
        out["hr_cap_used"] = hr_cap
        out["pct_above_hr_cap"] = (
            round(100.0 * len([h for h in have if h > hr_cap]) / len(have), 1)
            if have else None)
    if cadence:
        have = [c for c in cads if c is not None]
        out["cadence_in_band_pct"] = (
            round(100.0 * len([c for c in have
                               if cadence - band <= c <= cadence + band])
                  / len(have), 1) if have else None)

    # Drift and decoupling both split the moving portion in half. Drift is the
    # raw HR climb; decoupling divides it out by speed, which separates "I got
    # tired" from "I ran faster" — the distinction a summary row destroys.
    half = len(moving) // 2
    if half:
        h1, h2 = _mean(hrs[:half]), _mean(hrs[half:])
        s1, s2 = _mean(spds[:half]), _mean(spds[half:])
        if h1 and h2:
            out["hr_drift_bpm"] = round(h2 - h1, 1)
            if s1 and s2:
                e1, e2 = s1 / h1, s2 / h2
                out["decoupling_pct"] = round(100.0 * (e2 - e1) / e1, 1)

    # Non-moving stretches before the last moving sample: a break inside the
    # session. A cooldown walk at the end is not a break and isn't counted.
    last_move = max(r["t"] for r in moving)
    gaps, stopped = [], None
    for r, m in mask:
        t = r["t"]
        if t > last_move:
            break
        if not m:
            stopped = t if stopped is None else stopped
        elif stopped is not None:
            if t - stopped >= 5:
                gaps.append(t - stopped)
            stopped = None
    out["walk_break_s"] = round(sum(gaps)) if gaps else 0
    return out


def summary_row(act, derived):
    """The session row itself, in ingest/schema.py's shape and units."""
    s = act.session
    dist_m, spd = s.get("total_distance"), s.get("avg_speed")
    cad = s.get("avg_cadence")   # already in the sport's unit; see activity.py
    row = {
        "datetime": act.start_local.strftime("%Y-%m-%d %H:%M:%S"),
        "date": act.start_local.strftime("%Y-%m-%d"),
        "type": act.type,
        "title": "",  # the CSV export owns this; "" never overwrites
        "distance_km": round(dist_m / 1000.0, 2) if dist_m else None,
        "duration_s": round(s.get("total_timer_time") or 0, 1) or None,
        "avg_hr": s.get("avg_hr"),
        "max_hr": s.get("max_hr"),
        "aerobic_te": s.get("total_training_effect"),
        "cadence": round(cad) if cad else None,
        "pace_s_per_km": round(1000.0 / spd) if spd else None,
        "ascent_m": s.get("total_ascent"),
        "stride_m": round(s["avg_step_length"] / 1000.0, 2)
                    if s.get("avg_step_length") else None,
        "vert_osc_cm": round(s["avg_vertical_oscillation"] / 10.0, 1)
                       if s.get("avg_vertical_oscillation") else None,
        "gct_ms": round(s["avg_stance_time"]) if s.get("avg_stance_time") else None,
        "gct_left_pct": round(s["avg_stance_time_balance"], 1)
                        if s.get("avg_stance_time_balance") else None,
        "calories": s.get("total_calories"),
    }
    row.update(derived)
    return row


# --- Finding the files -------------------------------------------------------
def folder_files(d=None):
    """Every readable activity file in the drop folder, oldest first."""
    d = d or ACTIVITIES
    if not os.path.isdir(d):
        return []
    out = [os.path.join(d, f) for f in sorted(os.listdir(d))
           if f.lower().endswith(readable()) and not f.startswith(".")]
    return sorted(out, key=os.path.getmtime)


def unreadable_files(d=None):
    """Files sitting in the folder that nothing here can open — worth saying,
    since 'I dropped it in and nothing happened' is the failure mode."""
    d = d or ACTIVITIES
    if not os.path.isdir(d):
        return []
    return [f for f in sorted(os.listdir(d))
            if not f.startswith(".") and not f.lower().endswith(readable())
            and os.path.isfile(os.path.join(d, f))
            and f.lower() != "readme.md"]


# --- The hook build.py uses --------------------------------------------------
def activity_rows(cfg=None, report=None):
    """Every activity file in the drop folder, as session rows.

    Called by build.py, so a dropped file needs no separate command. The rows
    carry both the vendor summary and the derived fields; build.py puts them
    through the same TRIMP and upsert path as the CSV rows, which is what keeps
    a .fit-only session from being invisible to the load model.
    """
    cfg = cfg or CFG
    opt = settings(cfg)
    rows = []
    for path in folder_files():
        name = os.path.basename(path)
        try:
            act = activity.read(path, cfg, report)
        except activity.ActivityError as e:
            if report:
                report.warn(f"{name}: {e}")
            continue
        d = analyse(act, opt["hr_cap"], opt["cadence"], opt["band"], opt["floor"])
        if not d:
            if report:
                report.warn(f"{name}: no speed or cadence readings, so pace/HR "
                            f"detail couldn't be refined for this session")
            continue
        row = summary_row(act, d)
        row["_source_file"] = name           # for the journal marker; dropped below
        rows.append(row)
        if report:
            # One file is one session: 1 read, 1 kept. The record count belongs
            # in the note, not the skipped column — nothing was skipped.
            report.file(name, "activity", 1, 1, "",
                        f"{act.type} {act.start_local:%Y-%m-%d %H:%M}, "
                        f"{len(act.records)} records")
    for f in unreadable_files():
        if report:
            report.warn(f"data/activities/{f}: no reader handles that format "
                        f"(have {', '.join(readable()) or 'none'}) — not read")
    return rows


# --- Saying it ---------------------------------------------------------------
def mmss(sec):
    return f"{int(sec) // 60}:{int(sec) % 60:02d}" if sec else "-"


def n(v):
    """140.0 -> '140'. Thresholds arrive as floats and reading 'above the
    140.0 cap' in a journal line is a small, permanent annoyance."""
    if v is None:
        return "-"
    return str(int(v)) if float(v) == int(float(v)) else str(v)


def report_text(act, row, d, hr_cap, cadence, band, floor):
    s = act.session
    L = [f"\n{act.type} — {act.start_local:%a %d %b %Y, %H:%M}   ({act.name})",
         "=" * 64]
    L.append(f"{row['distance_km']} km in {mmss(row['duration_s'])}"
             f"   avg {mmss(row['pace_s_per_km'])}/km"
             f"   HR {row['avg_hr']}/{row['max_hr']}"
             f"   cadence {row['cadence']} spm")
    if row.get("stride_m"):
        L.append(f"step {row['stride_m']} m   vert osc {row['vert_osc_cm']} cm"
                 f"   GCT {row['gct_ms']} ms   power {s.get('avg_power') or '-'} W")

    laps = [lp for lp in act.laps if (lp.get("total_distance") or 0) >= 50]
    if len(laps) > 1:
        L.append("\nLaps")
        L.append(f"{'#':>3} {'dist':>8} {'time':>8} {'pace':>9} {'HR':>9} {'cad':>5}")
        for i, lp in enumerate(laps, 1):
            dm, tt = lp["total_distance"], lp.get("total_timer_time") or 0
            c = lp.get("avg_cadence")
            L.append(f"{i:>3} {dm:>7.0f}m {mmss(tt):>8} "
                     f"{mmss(tt / (dm / 1000.0)):>6}/km "
                     f"{str(lp.get('avg_hr') or '-'):>4}/"
                     f"{str(lp.get('max_hr') or '-'):<4} "
                     f"{round(c) if c else '-':>5}")

    L.append("\nWhile moving" + (f" (cadence >= {n(floor)} spm)" if act.is_run else ""))
    L.append(f"  time {mmss(d.get('moving_time_s'))} of {mmss(row['duration_s'])}"
             f"   HR {d.get('moving_avg_hr') or '-'}"
             f"   cadence {d.get('moving_cadence') or '-'} spm"
             f"   pace {mmss(d.get('moving_pace_s_per_km'))}/km")
    if d.get("moving_cadence") and row.get("cadence"):
        gap = d["moving_cadence"] - row["cadence"]
        if abs(gap) >= 3:
            L.append(f"  ! the CSV reports {row['cadence']} spm for this session"
                     f" — {gap:+d} spm off what you actually held, because it "
                     f"averages in the walking")
    if hr_cap and d.get("pct_above_hr_cap") is not None:
        L.append(f"  HR above {n(hr_cap)}: {d['pct_above_hr_cap']}% of moving time")
    if cadence and d.get("cadence_in_band_pct") is not None:
        L.append(f"  cadence in {n(cadence - band)}-{n(cadence + band)}: "
                 f"{d['cadence_in_band_pct']}% of moving time")
    if d.get("walk_break_s"):
        L.append(f"  breaks inside the session: {d['walk_break_s']} s")
    if d.get("hr_drift_bpm") is not None:
        L.append(f"  HR drift 1st->2nd half: {d['hr_drift_bpm']:+} bpm"
                 f"   decoupling {d.get('decoupling_pct', 0):+}%")
    return "\n".join(L)


def journal_line(act_name, row, d, hr_cap, cadence, band):
    """One line, in the grammar config.journal_grammar describes: a date, then
    prose. No invented subjective scores — those are the athlete's to give."""
    bits = [f"{row['distance_km']} km in {mmss(row['duration_s'])}"]
    if d.get("moving_cadence"):
        b = f"cadence {d['moving_cadence']} spm while moving"
        if d.get("cadence_in_band_pct") is not None:
            b += (f" ({d['cadence_in_band_pct']:.0f}% inside "
                  f"{n(cadence - band)}-{n(cadence + band)})")
        if row.get("cadence") and abs(d["moving_cadence"] - row["cadence"]) >= 3:
            b += f", though the session average reads {row['cadence']}"
        bits.append(b)
    if d.get("moving_avg_hr"):
        b = f"HR {d['moving_avg_hr']} avg"
        if hr_cap and d.get("pct_above_hr_cap") is not None:
            b += (f", {d['pct_above_hr_cap']:.0f}% of moving time above the "
                  f"{n(hr_cap)} cap")
        bits.append(b)
    if d.get("moving_pace_s_per_km"):
        bits.append(f"pace {mmss(d['moving_pace_s_per_km'])}/km")
    if row.get("stride_m"):
        bits.append(f"step {row['stride_m']} m")
    if d.get("decoupling_pct") is not None:
        bits.append(f"decoupling {d['decoupling_pct']:+}%")
    return (f"{row['date']} | note: {row['type']} — " + "; ".join(bits)
            + f". [from {act_name}]")


# --- Writing -----------------------------------------------------------------
def save_journal(line, marker, dry=False):
    """Append unless this activity is already in there. -> True if written.

    The marker is the filename, which is why journal_line puts it in the text:
    it makes re-running build.py idempotent without a separate ledger of what
    has been analysed.
    """
    path = os.path.join(ROOT, "journal.md")
    existing = ""
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            existing = f.read()
    if marker and f"[from {marker}]" in existing:
        return False
    if dry:
        return True
    with open(path, "a", encoding="utf-8") as f:
        f.write("\n" + line + "\n")
    return True


def journal_for_rows(rows, cfg=None, dry=False):
    """Write a journal line per new activity. -> the lines actually written.

    build.py calls this after upserting, so a dropped file becomes a sentence
    the coach reads without anyone typing it.
    """
    cfg = cfg or CFG
    if not (cfg.get("analysis", {}) or {}).get("journal", True):
        return []
    opt = settings(cfg)
    written = []
    for row in rows:
        marker = row.get("_source_file")
        d = {k: row.get(k) for k in DERIVED if row.get(k) is not None}
        line = journal_line(marker, row, d, opt["hr_cap"], opt["cadence"],
                            opt["band"])
        if save_journal(line, marker, dry):
            written.append(line)
    return written


# --- CLI ---------------------------------------------------------------------
def resolve(arg, want_all):
    """-> [paths]. No argument means the newest file in the drop folder, which
    is nearly always the run you just did."""
    if arg:
        if os.path.isdir(arg):
            found = folder_files(arg)
            return found if want_all else found[-1:]
        return [arg]
    found = folder_files()
    if not found:
        return []
    return found if want_all else found[-1:]


def analyse_one(path, a, cfg):
    import build   # local: build.py imports this module, so keep it one-way

    try:
        act = activity.read(path, cfg)
    except activity.ActivityError as e:
        print(f"Cannot read it: {e}")
        return 1

    d = analyse(act, a.hr_cap, a.cadence, a.band, a.floor)
    if not d:
        print(f"{act.name}: no usable records — nothing to analyse.")
        return 1
    row = summary_row(act, d)

    hr_max = cfg["athlete"]["hr_max_override"]
    if not hr_max:
        seen = [build.fnum(r, "max_hr")
                for r in (build.read_csv(build.SESSIONS)[1]
                          if os.path.exists(build.SESSIONS) else [])]
        seen = [v for v in seen if v] + [row["max_hr"] or 0]
        hr_max = max(seen) + 5 if seen else 185
    rhrs = [build.fnum(r, "rhr")
            for r in (build.read_csv(build.DAILY)[1]
                      if os.path.exists(build.DAILY) else [])]
    rhrs = [v for v in rhrs if v]
    hr_rest = round(sum(rhrs) / len(rhrs)) if rhrs \
        else cfg["athlete"]["hr_rest_fallback"]

    row["trimp"] = build.trimp(row, hr_rest, hr_max)
    row["mech_km"] = build.mech_km(row)

    print(report_text(act, row, d, a.hr_cap, a.cadence, a.band, a.floor))
    print(f"\n  TRIMP {row['trimp']} (HR_rest={hr_rest}, HR_max={n(hr_max)})"
          f"   {cfg['mechanical']['label']} {row['mech_km']}")

    existing = {}
    if os.path.exists(build.SESSIONS):
        for r in build.read_csv(build.SESSIONS)[1]:
            existing[(r.get("datetime", ""), r.get("type", ""))] = r
    matched = (row["datetime"], row["type"]) in existing

    if not a.dry_run:
        build.upsert(build.SESSIONS, [{k: v for k, v in row.items()
                                       if k != "_source_file"}],
                     ["datetime", "type"], drop=build.LEGACY_COLS)
    verb = ("would merge onto" if a.dry_run else "merged onto") if matched \
        else ("would create" if a.dry_run else "created")
    print(f"\n{verb} session ({row['datetime']}, {row['type']}) in "
          f"{os.path.relpath(build.SESSIONS, ROOT)}")
    if not matched:
        print("  ! no existing row had that key. If this activity is also in "
              "your CSV export under a different timestamp or type you now have "
              "it twice, and its load counts twice — check data/sessions.csv.")
    print(f"  derived: {', '.join(k for k in DERIVED if k in row)}")

    if not a.no_journal:
        line = journal_line(act.name, row, d, a.hr_cap, a.cadence, a.band)
        if save_journal(line, act.name, a.dry_run):
            print(f"\n{'would append' if a.dry_run else 'appended'} to "
                  f"journal.md:\n  {line}")
        else:
            print("\njournal.md already has a line for this file — left alone.")
    return 0


def main(argv=None):
    cfg = config.load()
    opt = settings(cfg)
    ap = argparse.ArgumentParser(
        description="Close analysis of one activity file (.fit).")
    ap.add_argument("path", nargs="?",
                    help="file or folder; default: newest in data/activities/")
    ap.add_argument("--all", action="store_true",
                    help="every file in the folder, not just the newest")
    ap.add_argument("--hr-cap", type=float, default=opt["hr_cap"],
                    help="HR ceiling this session was prescribed")
    ap.add_argument("--cadence", type=float, default=opt["cadence"],
                    help="cadence target, spm")
    ap.add_argument("--band", type=float, default=opt["band"],
                    help="+/- spm counted as on target")
    ap.add_argument("--floor", type=float, default=opt["floor"],
                    help="spm below which you are not running")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the analysis, write nothing")
    ap.add_argument("--no-journal", action="store_true")
    a = ap.parse_args(argv)

    paths = resolve(a.path, a.all)
    if not paths:
        os.makedirs(ACTIVITIES, exist_ok=True)
        print(f"Nothing to analyse. Put activity files "
              f"({', '.join(readable()) or 'no readers installed'}) in "
              f"{os.path.relpath(ACTIVITIES, ROOT)}/ and run this again —\n"
              f"or just press Rebuild in the dashboard, which reads the same "
              f"folder.")
        for f in unreadable_files():
            print(f"  ! {f} is there, but no reader handles that format")
        return 1

    rc = 0
    for p in paths:
        rc |= analyse_one(p, a, cfg)
    if not a.dry_run:
        print("\nRun `python3 build.py` to fold it into context.md.")
    return rc


if __name__ == "__main__":
    sys.exit(main())
