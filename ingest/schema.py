#!/usr/bin/env python3
"""The contract between ingest and everything else.

An adapter reads whatever your source exports and returns three things:

  sessions  list of dicts   one per workout          keyed by (datetime, type)
  sleep     dict date->dict one per night            keyed by date
  hrv       dict date->dict one per HRV reading      keyed by date

Those are the only records the engine ever sees. TRIMP, ACWR, the accumulating
database, context.md, the dashboard and the coach are all downstream of this
file and know nothing about Garmin, Strava, or CSV.

Two rules that make it safe to write an adapter:

1.  Every field is optional except the keys. Leave out what your source doesn't
    export — None means "no reading", and the engine handles that everywhere.
    A source with no HRV simply returns {} for hrv.
2.  Extra session fields are kept. Anything you add rides through into
    data/sessions.csv and can be named as config.form_metric.field, so an
    adapter can surface power, stroke rate or SWOLF without a code change
    elsewhere. Extra *sleep* and *hrv* fields are dropped — the daily table has
    fixed columns — and validate() will say so rather than lose them quietly.

Units are part of the contract. Getting one wrong is the failure mode that
produces plausible, wrong numbers instead of an error, so they're spelled out
per field below and validate() checks what it can.
"""

from __future__ import annotations

import re

# Bumped only when a change here could break an adapter that isn't in this repo
# — a field renamed, a unit changed, a key made required. Adding an optional
# field doesn't count. Adapters declare the version they were written against;
# `python3 -m ingest` flags the mismatch, so an upstream pull tells you your
# adapter needs a look instead of quietly producing wrong numbers.
CONTRACT = 1

# --- Sessions ----------------------------------------------------------------
SESSION_KEY = ("datetime", "type")

SESSION_FIELDS = {
    "datetime": "REQUIRED. 'YYYY-MM-DD HH:MM:SS', local wall-clock time. With "
                "`type` this is the dedupe key across re-exports, so it must be "
                "stable — same workout, same string, every export.",
    "date": "REQUIRED. 'YYYY-MM-DD', the local date of `datetime`.",
    "type": "REQUIRED. Activity type as your source names it ('Running', "
            "'Ride', 'Lap Swimming'). Free text: config matches it by "
            "substring, so keep the source's own vocabulary rather than "
            "translating it.",
    "title": "Session name. '' if none.",
    "distance_km": "Distance in config.source.distance_unit — km unless you set "
                   "'mi'. The field name is historical; the unit follows config.",
    "duration_s": "Moving/elapsed time in SECONDS (float). Pick whichever your "
                  "source calls the training duration and be consistent.",
    "avg_hr": "Average heart rate, bpm. The single most load-bearing field "
              "here: no avg_hr means no TRIMP, which means no load at all.",
    "max_hr": "Max heart rate, bpm. Also feeds the HR_max estimate when "
              "config.athlete.hr_max_override is null.",
    "aerobic_te": "Aerobic training effect, 0-5, if your source scores one.",
    "cadence": "Average cadence, steps/min (or rpm, strokes/min — whatever the "
               "sport counts). Default config.form_metric.field.",
    "pace_s_per_km": "Average pace in SECONDS per distance unit.",
    "ascent_m": "Total ascent, metres.",
    "stride_m": "Average stride length, metres.",
    "vert_osc_cm": "Average vertical oscillation, centimetres.",
    "gct_ms": "Average ground contact time, milliseconds.",
    "gct_left_pct": "Left-side share of ground contact time, percent (49.5).",
    "calories": "Energy, kcal.",
}

SESSION_NUMERIC = {
    "distance_km", "duration_s", "avg_hr", "max_hr", "aerobic_te", "cadence",
    "pace_s_per_km", "ascent_m", "stride_m", "vert_osc_cm", "gct_ms",
    "gct_left_pct", "calories",
}

# --- Sleep -------------------------------------------------------------------
SLEEP_FIELDS = {
    "date": "REQUIRED. 'YYYY-MM-DD'. The morning you woke up on — a night is "
            "filed under its wake date, matching how every export does it.",
    "sleep_score": "Vendor sleep score, 0-100.",
    "rhr": "Overnight resting heart rate, bpm. Feeds the HR_rest constant in "
           "TRIMP and the RHR-above-baseline readiness flag, so it earns its "
           "keep even without a sleep score.",
    "body_battery": "Vendor energy score, 0-100. Cosmetic; omit freely.",
    "respiration": "Average overnight breaths/min.",
    "hrv_7d": "Rolling 7-day HRV average, ms, if the sleep export carries one. "
              "Falls back to the hrv record's value when absent.",
    "sleep_quality": "Vendor's word for the night ('Good'). Free text.",
    "sleep_min": "Time asleep, MINUTES (int).",
    "sleep_need_min": "Vendor's sleep need, MINUTES.",
    "sleep_debt_min": "need - actual, MINUTES. Compute it if you have both; "
                      "leave None if you don't. Drives the 3-day debt flag.",
    "bedtime": "Free text as exported ('23:14').",
    "waketime": "Free text as exported ('06:52').",
}

SLEEP_NUMERIC = {"sleep_score", "rhr", "body_battery", "respiration", "hrv_7d",
                 "sleep_min", "sleep_need_min", "sleep_debt_min"}

# --- HRV ---------------------------------------------------------------------
HRV_FIELDS = {
    "date": "REQUIRED. 'YYYY-MM-DD'.",
    "hrv_night": "Overnight average HRV, milliseconds.",
    "hrv_base_lo": "Low end of the vendor's personal baseline band, ms.",
    "hrv_base_hi": "High end of the baseline band, ms.",
}

HRV_NUMERIC = {"hrv_night", "hrv_base_lo", "hrv_base_hi"}

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ISO_DT = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2})?$")


def blank_session(**kw):
    """A session dict with every known field present and None. Optional — a
    partial dict validates fine — but handy for keeping a column order."""
    r = {k: None for k in SESSION_FIELDS}
    r.update(kw)
    return r


def _coerce(rec, numeric, where, report):
    """Numbers that arrived as strings are a normal adapter slip. Fix them,
    say so once per field, and keep going."""
    for k in list(rec):
        v = rec[k]
        if k not in numeric or v is None or isinstance(v, (int, float)):
            continue
        try:
            rec[k] = float(str(v).replace(",", "."))
        except (TypeError, ValueError):
            rec[k] = None
            report.warn(f"{where}: {k}={v!r} is not a number — dropped")


def validate(got, report):
    """Check and normalize what an adapter returned, in place.

    Anything recoverable is repaired and reported; anything unusable is dropped
    with a line naming the record. The engine can then assume the contract holds.
    Returns the same Ingested object.
    """
    # -- sessions --
    ok = []
    for i, s in enumerate(got.sessions):
        if not isinstance(s, dict):
            report.warn(f"sessions[{i}]: not a dict — dropped")
            continue
        where = f"session {s.get('datetime') or i}"
        dt = s.get("datetime")
        if not dt or not _ISO_DT.match(str(dt)):
            report.warn(f"{where}: datetime must be 'YYYY-MM-DD HH:MM:SS' "
                        f"— dropped")
            continue
        s["datetime"] = str(dt).replace("T", " ")
        if len(s["datetime"]) == 16:
            s["datetime"] += ":00"
        if not s.get("date") or not _ISO_DATE.match(str(s["date"])):
            s["date"] = s["datetime"][:10]
        s["type"] = (s.get("type") or "").strip()
        s["title"] = s.get("title") or ""
        _coerce(s, SESSION_NUMERIC, where, report)
        ok.append(s)
    dropped = len(got.sessions) - len(ok)
    if dropped:
        report.warn(f"sessions: {dropped} dropped by the schema check above")
    got.sessions = ok

    extra = sorted({k for s in ok for k in s} - set(SESSION_FIELDS))
    if extra:
        report.note(f"extra session fields kept: {', '.join(extra)} "
                    f"— usable as config.form_metric.field")

    # -- sleep and hrv --
    got.sleep = _clean_daily(got.sleep, SLEEP_FIELDS, SLEEP_NUMERIC, "sleep",
                             report)
    got.hrv = _clean_daily(got.hrv, HRV_FIELDS, HRV_NUMERIC, "hrv", report)
    return got


def _clean_daily(recs, fields, numeric, kind, report):
    if not isinstance(recs, dict):
        report.warn(f"{kind}: adapter returned {type(recs).__name__}, expected "
                    f"a dict keyed by 'YYYY-MM-DD' — ignored")
        return {}
    out, unknown = {}, set()
    for k, v in recs.items():
        if not _ISO_DATE.match(str(k)) or not isinstance(v, dict):
            report.warn(f"{kind}[{k!r}]: key must be 'YYYY-MM-DD' and the value "
                        f"a dict — dropped")
            continue
        rec = {f: v.get(f) for f in fields if f in v}
        unknown |= set(v) - set(fields)
        rec["date"] = str(k)
        _coerce(rec, numeric, f"{kind} {k}", report)
        out[str(k)] = rec
    if unknown:
        report.warn(f"{kind}: field(s) {', '.join(sorted(unknown))} are not in "
                    f"the {kind} schema — dropped. The daily table has fixed "
                    f"columns; put per-session extras on sessions instead")
    return out
