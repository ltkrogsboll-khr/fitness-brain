#!/usr/bin/env python3
"""Configuration — everything sport- or limiter-specific lives here.

The engine (TRIMP, ACWR, the accumulating database, the policy→context→journal
loop) is sport-agnostic. What isn't: which activity types carry mechanical load,
what a good per-session form number looks like, what you log about how your body
felt, and what the coach calls itself.

DEFAULTS below are deliberately neutral — HR-based endurance training, one load
channel, no form metric — so an unconfigured install is generic rather than
shaped like whoever wrote it. `config.example.json` is a filled-in preset for a
runner managing shin load; copy it to `config.json` and edit.

`config.json` is gitignored: it names your limiter, so it stays on your machine.
Missing keys fall back to DEFAULTS, so a partial config is fine and adding new
keys upstream won't break yours.
"""

from __future__ import annotations

import json
import os

ROOT = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(ROOT, "config.json")

# Nested objects merge key-by-key; these replace wholesale instead, because
# "here is my weight table" should be able to *remove* an entry, not only add.
REPLACE_WHOLE = {"weights", "fields", "match", "kinds", "metre_distance_types"}

DEFAULTS = {
    "athlete": {
        # No lab test? Leave hr_max_override null and the highest HR ever seen
        # in the data (plus a small margin) is used instead.
        "hr_max_override": None,
        # Only used until there is enough sleep data to average a real one.
        "hr_rest_fallback": 55,
    },

    # One training block. Seven days is the common case, not a requirement:
    # this sets how the load charts bucket their bars, the window the primary
    # cap is measured over, and the noun the dashboard uses in prose. ACWR is
    # left on its standard 7:28-day definition whatever you put here.
    "cycle": {
        "days": 7,
        # Both blank derive themselves: "week" / "per week" at seven days,
        # "cycle" / "per N days" otherwise. Set them to call it a microcycle,
        # a block, a training week — whatever you actually say out loud.
        "label": "",
        "per": "",
    },

    # Channel 1: cardiovascular work, in TRIMP. Applies to any activity with a
    # heart rate, so it is always on.
    "aerobic": {
        "label": "Aerobic",
        "unit": "TRIMP",
        "acwr_ceiling": 1.5,
        "rationale": "",
    },

    # Channel 2: whatever tissue adapts slower than your heart does. Distance
    # scaled per activity type. Off by default — most sports don't need it.
    "mechanical": {
        "enabled": False,
        "label": "Mechanical",
        "noun": "mechanical load",
        "unit": "weighted km",
        "acwr_ceiling": 1.3,
        "warn_ratio": 1.15,
        "weights": {},
        "rationale": "",
    },

    # The headline volume number: distance from the activity types you actually
    # train for, unweighted. Off by default.
    "primary": {
        "enabled": False,
        "label": "Primary",
        "unit": "km",
        "match": [],
        # Ceiling on volume over one cycle. `weekly_cap` is the old name for
        # the same number and still works.
        "cap": None,
        "weekly_cap": None,
        "cap_note": "",
    },

    # Any per-session number worth holding to a target — cadence, stroke rate,
    # power. Read straight from a column of data/sessions.csv.
    "form_metric": {
        "enabled": False,
        "field": "cadence",
        "label": "Cadence",
        "unit": "spm",
        "target": None,
        "floor": None,
        "rationale": "",
    },

    # Pace-at-fixed-HR scatter: aerobic efficiency, the metric that can't be
    # gamed by pushing harder.
    "benchmark": {
        "enabled": False,
        "hr": 140,
        # Drop sessions whose form metric is below this before plotting — a
        # run-walk drags pace down without saying anything about efficiency.
        "min_form": None,
        "rationale": "",
    },

    # Close analysis of a single activity from its .fit file (analyze.py).
    # These are what the *current plan* asked of a session, so they change when
    # the plan does -- every one is overridable per run on the command line.
    "analysis": {
        # HR ceiling the session was prescribed. null disables the measure
        # rather than inventing one; `--hr-cap 140` sets it for one run.
        "hr_cap": None,
        # Cadence target. Falls back to form_metric.target when null.
        "cadence_target": None,
        "cadence_band": 5,          # +/- spm still counted as on target
        # Below this you are walking, not running, and the samples are excluded
        # from the moving averages. This is the whole point of reading the file:
        # a walk home drags a session average somewhere you never actually were.
        # Walking runs 100-120 spm and running 150+, so the boundary sits at
        # 140 -- put it at 100 and a cooldown walk reads as running.
        # Applies to runs only; other sports use "was I moving at all".
        "walk_cadence_floor": 140,
        "journal": True,            # also append a line to journal.md
    },

    "readiness": {
        "sleep_score_warn": 70,
        "sleep_debt_h": 2.0,
        "rhr_above_baseline": 3,
        # HRV below baseline is one signal; a veto needs RHR this high too.
        "rhr_veto": None,
    },

    "journal": {
        # One number input each. `split` turns a field into per-side inputs
        # (e.g. ["L","R"] → `shin L2/R1`); omit it for a single value.
        "fields": [
            {"key": "soreness", "label": "Soreness", "min": 0, "max": 10,
             "default": 0},
        ],
        "placeholder": "note — how it felt, how you woke up…",
    },

    "plan": {
        # Kinds usable in a plan's ```cycle block (```week is the old fence
        # name and still parses), and how each one decides it was done.
        # complete_when: primary_volume | any_activity |
        # activity_matches (with `match`) | always
        "kinds": {
            "work": {"label": "WORK", "complete_when": "any_activity"},
            "rest": {"label": "REST", "complete_when": "always"},
        },
    },

    # Where the data comes from. Everything here is read by the ingest adapter
    # and by nothing else -- the engine downstream sees only the normalized
    # records in ingest/schema.py.
    #
    # If a number goes missing from the dashboard, run build.py and read the
    # Ingest report: unmatched files, absent columns and unparseable dates are
    # all named there rather than skipped silently. `python3 -m ingest --check`
    # goes further and shows how many records carried each field.
    "source": {
        "name": "your watch",

        # Which adapter in ingest/adapters/ reads your export. The default is
        # column-mapped CSV: it fits Garmin Connect out of the box, and fits
        # other vendors by renaming the columns below. Anything shaped
        # differently gets its own adapter -- `python3 -m ingest` lists them,
        # and .claude/skills/ingest-adapter walks an agent through writing one.
        "adapter": "garmin_csv",

        # Free-form, for adapters that need settings of their own. Nothing in
        # this repo reads it; your adapter does, via cfg["source"]["options"].
        "options": {},

        "files": {"activities": "activities", "sleep": "sleep", "hrv": "hrv"},

        # Cell values that mean "no reading".
        "missing": ["", "--", "---", "N/A", "n/a", "null"],

        # Decimal convention. "auto" decides per file from the values themselves
        # ('6,56' votes comma, '6.56' votes dot); thousands separators are the
        # ambiguous case and get no vote. Force it if a file has too few
        # fractional numbers to sniff.
        "decimal": "auto",  # auto | comma | dot

        # Tried in order against the activity timestamp column. Deliberately
        # unambiguous by default -- adding both %d/%m/%Y and %m/%d/%Y here would
        # silently pick one. Add the single format your export actually uses.
        "datetime_formats": ["%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S%z",
                             "%Y-%m-%d %H:%M"],
        # Same, for the sleep and HRV date columns.
        "date_formats": ["%Y-%m-%d"],

        # Shapes a duration or pace cell may take, tried in order:
        #   hms      '00:44:47', '6:50'  -- right-aligned, last part is seconds
        #   hm       '6:24'              -- left-aligned, first part is hours
        #   hm_text  '6h 24min'          -- needs a unit letter
        #   minutes  '384'               -- bare number of minutes
        #   seconds  '23087'             -- bare number of seconds
        "duration_formats": {
            "activity": ["hms"],
            "sleep": ["hm_text", "hm", "minutes"],
        },

        # What distances are exported in. Labels the dashboard, and sets what
        # metre_distance_types below converts into.
        "distance_unit": "km",  # km | mi

        "activity_columns": {
            "type": "Activity Type", "date": "Date", "title": "Title",
            "distance": "Distance", "duration": "Time", "avg_hr": "Avg HR",
            "max_hr": "Max HR", "aerobic_te": "Aerobic TE",
            "cadence": "Avg Run Cadence", "pace": "Avg Pace",
            "ascent": "Total Ascent", "stride": "Avg Stride Length",
            "vert_osc": "Avg Vertical Oscillation",
            "gct": "Avg Ground Contact Time", "gct_balance": "Avg GCT Balance",
            "calories": "Calories",
        },
        # `date` null means "the first column", which is where both of these
        # exports put it.
        "sleep_columns": {
            "date": None,
            "score": "Score", "rhr": "Resting Heart Rate",
            "body_battery": "Body Battery", "respiration": "Respiration",
            "hrv_7d": "HRV Status", "quality": "Quality",
            "duration": "Duration", "need": "Sleep Need",
            "bedtime": "Bedtime", "waketime": "Wake Time",
        },
        # If the date column holds year-less days ('8 Aug'), build.py infers the
        # year by walking backwards from the newest row. English month
        # abbreviations only; anything matching date_formats is used as-is.
        "hrv_columns": {"date": None, "overnight": "Overnight HRV",
                        "baseline": "Baseline"},
        # Distances exported in metres rather than distance_unit, by
        # activity-type substring. Applied before anything else reads the number.
        "metre_distance_types": ["Swim"],
    },

    "coach": {
        "role": "endurance coach",
        "system": "a policy-driven training system",
        # Appended verbatim to the system prompt. Say what should send the
        # athlete to a professional rather than to a plan adjustment.
        "safety": "",
        # What the journal is for, in the coach's own terms.
        "journal_examples": "how a session actually felt, next-morning symptoms, "
                            "a life event affecting training, equipment changes, "
                            "a goal change",
    },
}


def _merge(base, over):
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict) and k not in REPLACE_WHOLE:
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


_cache = None


def load(force=False):
    """Read config.json over DEFAULTS. Cached; call with force=True to re-read."""
    global _cache
    if _cache is not None and not force:
        return _cache
    user = {}
    if os.path.exists(PATH):
        try:
            with open(PATH, encoding="utf-8") as f:
                user = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"config.json ignored ({type(e).__name__}: {e}) — using defaults.")
    _cache = _derive(_merge(DEFAULTS, user))
    return _cache


def _derive(cfg):
    """Fill in the values computed from other values, once, at load.

    Done here rather than at each call site so the dashboard, context.md and
    the coach prompt cannot end up describing the same cycle differently.
    """
    cy = cfg["cycle"]
    try:
        cy["days"] = max(1, int(cy["days"]))
    except (TypeError, ValueError):
        cy["days"] = 7
    if not cy.get("label"):
        cy["label"] = "week" if cy["days"] == 7 else "cycle"
    if not cy.get("per"):
        cy["per"] = "per week" if cy["days"] == 7 else f"per {cy['days']} days"

    p = cfg["primary"]
    if p.get("cap") is None:
        p["cap"] = p.get("weekly_cap")
    p["weekly_cap"] = p["cap"]
    return cfg


def journal_grammar(cfg):
    """The exact journal line shape, as both the UI and the model must write it.

    Derived from journal.fields so there is one definition, not two that drift.
    """
    parts = []
    for f in cfg["journal"]["fields"]:
        if f.get("split"):
            body = "/".join(f"{s}#" for s in f["split"])
            parts.append(f"{f['key']} {body}")
        else:
            parts.append(f"{f['key']} #")
    return " | ".join(["YYYY-MM-DD"] + parts + ["note: ..."])


if __name__ == "__main__":
    print(json.dumps(load(), indent=2))
