#!/usr/bin/env python3
"""One session at full resolution — the contract, and the readers behind it.

    data/activities/*  ->  [ reader ]  ->  Activity  ->  [ analyze.py ]
                            swappable       this file     never changes

The sibling of `ingest/adapters/`, for the other ingestion path. An adapter
reads one bulk export of everything; a reader reads one file describing one
session, second by second. Both exist so that a new data source is a new file
in this folder rather than an edit to the engine.

The difference is how one gets chosen. A bulk export folder belongs to a single
vendor, so `config.source.adapter` names the adapter. Single-session files are
self-describing — a `.fit` is a `.fit` whoever wrote it — so readers are keyed
by **file extension** and picked per file. A folder holding a `.fit` from your
watch and a `.gpx` someone sent you needs no configuration at all.

A reader is one Python file with one function:

    CONTRACT = 1                      # version of the shape below
    DESCRIPTION = "Garmin/ANT+ .fit"
    EXTENSIONS = (".fit",)

    def read(path, cfg=None):
        return Activity(...)

Drop it in `ingest/readers/local/` (yours, upstream never touches that folder)
or `ingest/readers/` (shipped). A local file shadows a shipped one of the same
name, so you can bend `fit` without editing a tracked file.

    python3 -m ingest --readers    list readers and the extensions they claim

--- The shape ---------------------------------------------------------------

`Activity` holds three things, and every field in them is optional except where
marked. Missing means "not recorded"; analyze.py degrades a measure rather than
failing.

session   dict, the whole-session summary
laps      list of dicts, one per lap
records   list of dicts, one per sample — the reason this path exists

UNITS ARE THE CONTRACT. Getting one wrong produces plausible wrong numbers
instead of an error, which is the worst failure this repo can have.

  session / lap:
    total_distance          metres
    total_timer_time        seconds, moving time
    total_elapsed_time      seconds, wall time
    avg_hr, max_hr          bpm
    avg_cadence, max_cadence   see CADENCE below
    avg_speed, max_speed    metres/second
    total_ascent, descent   metres
    total_calories          kcal
    total_training_effect   vendor score, 0-5
    avg_power, max_power    watts
    avg_step_length         millimetres
    avg_vertical_oscillation   millimetres
    avg_stance_time         milliseconds
    avg_stance_time_balance    percent on the left

  records:
    t                       REQUIRED. Seconds from the session start, float.
                            Not a clock time — readers convert, so that nothing
                            downstream has to know a vendor's epoch.
    heart_rate              bpm
    cadence                 see CADENCE below
    speed                   metres/second
    distance                metres, cumulative from the start
    power                   watts
    altitude                metres
    step_length             millimetres
    vertical_oscillation    millimetres
    stance_time             milliseconds
    position_lat, position_long   degrees

CADENCE is in the sport's own unit: steps per minute for anything on foot,
revolutions per minute for cycling. Several formats store running cadence in
revolutions and expect the reader to double it — do that doubling **in the
reader**, so no code downstream has to remember which sport it is looking at.
It is the single easiest thing to get wrong here, and a session that reads 85
instead of 170 looks like a form collapse rather than a bug.
"""

from __future__ import annotations

import ast
import importlib.util
import os
import sys

# Bumped only when a change here could break a reader living outside this repo
# — a field renamed, a unit changed. Adding an optional field doesn't count.
CONTRACT = 1

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
READER_DIRS = [
    ("shipped", os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "readers")),
    ("local", os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "readers", "local")),
]


class ActivityError(Exception):
    """A file no reader can open, or a reader that won't import."""


class Activity:
    """One session, decoded. Readers build these; analyze.py consumes them.

    `start_local` is the load-bearing field: it is half the key into
    data/sessions.csv, and it must be local wall-clock time, matching how every
    bulk export writes it. Read the offset out of the file rather than applying
    the machine's timezone, or every session recorded on holiday lands in the
    wrong hour.
    """

    def __init__(self, path, type, start_local, session=None, laps=None,
                 records=None, is_run=False, title=""):
        self.path = path
        self.name = os.path.basename(path)
        self.type = type                  # vendor's own word: 'Running', 'Ride'
        self.start_local = start_local    # datetime, wall clock, no tzinfo
        self.session = dict(session or {})
        self.laps = list(laps or [])
        self.records = list(records or [])
        # Whether a cadence floor can separate working from walking. True for
        # runs only: a walk's own cadence sits exactly where that floor goes.
        self.is_run = is_run
        self.title = title or ""

    def stream(self, field):
        """[(t, value)] for one record field, gaps dropped."""
        return [(r["t"], r[field]) for r in self.records
                if r.get("t") is not None and r.get(field) is not None]

    def __repr__(self):
        return (f"Activity({self.name}, {self.type}, {self.start_local}, "
                f"{len(self.records)} records, {len(self.laps)} laps)")


# --- The registry ------------------------------------------------------------
def available():
    """-> {name: {origin, path, description, contract, extensions}}, local last
    so it wins. Reads each file's header rather than importing it, so a broken
    reader still appears in the list instead of taking the list down."""
    found = {}
    for origin, d in READER_DIRS:
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if not fn.endswith(".py") or fn.startswith("_"):
                continue
            found[fn[:-3]] = {"origin": origin, "path": os.path.join(d, fn),
                              **_peek(os.path.join(d, fn))}
    return found


def _peek(path):
    """DESCRIPTION, CONTRACT and EXTENSIONS without executing the module."""
    out = {"description": "", "contract": None, "extensions": ()}
    try:
        with open(path, encoding="utf-8") as f:
            head = f.read(4000)
    except OSError:
        return out
    for line in head.splitlines():
        s = line.strip()
        if s.startswith("DESCRIPTION") and "=" in s and not out["description"]:
            out["description"] = s.split("=", 1)[1].strip().strip('"\'')
        if s.startswith("CONTRACT") and "=" in s and out["contract"] is None:
            try:
                out["contract"] = int(s.split("=", 1)[1].split("#")[0].strip())
            except ValueError:
                pass
        if s.startswith("EXTENSIONS") and "=" in s and not out["extensions"]:
            try:
                got = ast.literal_eval(s.split("=", 1)[1].split("#")[0].strip())
                out["extensions"] = tuple(str(e).lower() for e in got)
            except (ValueError, SyntaxError):
                pass
    return out


def by_extension():
    """-> {'.fit': reader_name}. Local shadows shipped; two shipped readers
    claiming one extension is a packaging bug, and last-sorted wins."""
    out = {}
    for name, info in available().items():
        for ext in info["extensions"]:
            out[ext] = name
    return out


def extensions():
    """Every extension some reader claims — what the drop folder accepts."""
    return tuple(sorted(by_extension()))


def load(name):
    """Import a reader by name. -> module"""
    have = available()
    if name not in have:
        known = ", ".join(sorted(have)) or "none found"
        raise ActivityError(f"no reader named {name!r}. Available: {known}")
    path = have[name]["path"]
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    spec = importlib.util.spec_from_file_location(f"ingest._reader_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception as e:      # a user-written file: say where, not just what
        raise ActivityError(f"{os.path.relpath(path, ROOT)} failed to import "
                            f"({type(e).__name__}: {e})") from e
    if not hasattr(mod, "read"):
        raise ActivityError(f"{os.path.relpath(path, ROOT)} has no read() "
                            f"function — see ingest/activity.py for the contract")
    mod.NAME = name
    return mod


def read(path, cfg=None, report=None):
    """Decode one activity file with whichever reader claims its extension."""
    ext = os.path.splitext(path)[1].lower()
    name = by_extension().get(ext)
    if not name:
        known = ", ".join(extensions()) or "none"
        raise ActivityError(
            f"{os.path.basename(path)}: no reader handles {ext or 'that'} "
            f"— readers exist for {known}. Writing one is a file in "
            f"ingest/readers/local/; see ingest/activity.py")
    mod = load(name)
    written_for = getattr(mod, "CONTRACT", None)
    if written_for is not None and written_for != CONTRACT and report:
        report.warn(f"reader {name!r} was written for activity contract "
                    f"v{written_for}, this repo is v{CONTRACT} — check "
                    f"ingest/activity.py for what changed")
    got = mod.read(path, cfg)
    if not isinstance(got, Activity):
        raise ActivityError(f"reader {name!r} returned {type(got).__name__}, "
                            f"expected ingest.activity.Activity")
    if got.start_local is None:
        raise ActivityError(f"{os.path.basename(path)}: reader {name!r} "
                            f"returned no start_local, which is the session key")
    return got
