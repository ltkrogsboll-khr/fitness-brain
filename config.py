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
        # Kinds usable in a plan's ```week block, and how each one decides it
        # was done. complete_when: primary_volume | any_activity |
        # activity_matches (with `match`) | always
        "kinds": {
            "work": {"label": "WORK", "complete_when": "any_activity"},
            "rest": {"label": "REST", "complete_when": "always"},
        },
    },

    # Where the CSVs come from. Files in data/raw/ are routed by substring match
    # on the filename, and columns are read by the header names below -- so a
    # different vendor's export is a config change, not a code change.
    "source": {
        "name": "your watch",
        "files": {"activities": "activities", "sleep": "sleep", "hrv": "hrv"},
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
        "sleep_columns": {
            "score": "Score", "rhr": "Resting Heart Rate",
            "body_battery": "Body Battery", "respiration": "Respiration",
            "hrv_7d": "HRV Status", "quality": "Quality",
            "duration": "Duration", "need": "Sleep Need",
            "bedtime": "Bedtime", "waketime": "Wake Time",
        },
        "hrv_columns": {"overnight": "Overnight HRV", "baseline": "Baseline"},
        # Distances exported in metres rather than km, by activity-type
        # substring. Applied before anything else reads the number.
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
    _cache = _merge(DEFAULTS, user)
    return _cache


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
