---
name: ingest-adapter
description: Make this repo read the user's fitness data — Strava, Polar, Coros, Whoop, Suunto, Apple Health, Intervals.icu, a hand-rolled CSV, or a single-activity file (.fit, .tcx, .gpx). Use when build.py reports skipped rows or empty columns, when the dashboard is missing a number, when the user says their export isn't Garmin's, or when a file dropped in data/activities/ wasn't read. Covers the config-only path, writing a bulk adapter in ingest/adapters/local/, and writing a single-session reader in ingest/readers/local/.
---

# Making this repo read someone else's data

There are **two ingestion paths**, and picking the right one is the first
decision. Both end in normalized records, and everything downstream (TRIMP,
ACWR, the CSVs, `context.md`, the dashboard, the coach) reads only those.

| | Bulk export | Single session |
|---|---|---|
| Folder | `data/raw/` | `data/activities/` |
| Shape | Weeks of workouts + sleep + HRV, one row per session | One file, one workout, sampled per second |
| Code | `ingest/adapters/` | `ingest/readers/` |
| Contract | `ingest/schema.py` | `ingest/activity.py` |
| Chosen by | `config.source.adapter` | **file extension** — no config |
| Inspect | `python3 -m ingest` | `python3 -m ingest --readers` |

Ask which problem you actually have. "My weekly export doesn't load" is an
adapter. "I dropped a `.tcx` in `data/activities/` and nothing happened" is a
reader. A user who wants per-second detail — HR inside a session, cadence
excluding walk breaks — needs a reader, and no amount of adapter work gets it,
because a bulk export only ever carries one average per session.

**Never edit `build.py`, `serve.py`, `index.html` or `config.py` for either.**
If a data-source problem seems to need a change there, the boundary is wrong —
say so instead of crossing it. The one exception is `config.json`, the user's
own untracked file.

## Path A — a bulk export (`data/raw/`)

### 1. Look at the actual file before deciding anything

```sh
ls data/raw/
head -3 "data/raw/<file>.csv"
```

Read the real header and two real rows. Do not write column names from memory
of what Strava or Polar "usually" exports — versions differ, locales differ,
and a wrong guess produces a silent column of nulls rather than an error.

Establish, per file: what is a row (one workout? one lap? one day?), the
timestamp format, the decimal convention, distance units, and how duration is
written.

### 2. Try the config-only path first

`garmin_csv` is column-mapped, not Garmin-specific. If the export is one CSV
per kind with one row per workout, it very likely fits already — just with
different header names. That path costs the user nothing to maintain, so
prefer it whenever it works.

In `config.json` under `source`, set the column names to match the header, add
the timestamp format to `datetime_formats`, and adjust `files` (filename
substrings), `distance_unit`, `duration_formats` and `missing` as needed. The
full key list with comments is in `config.py`'s `DEFAULTS["source"]`.

```sh
python3 -m ingest --check
```

Runs ingestion only — writes nothing. Prints the ingest report, then how many
records carried each field. **Read the coverage table**: a field at `0/120` is
a mapping that didn't land. Iterate here until it looks right.

### 3. Write an adapter when the shape differs

Config can't fix: one row per lap, JSON/SQLite, several files per period, sleep
stages that need summing, a distance column carrying its own unit, per-vendor
activity-type quirks.

```sh
cp ingest/adapters/_template.py ingest/adapters/local/<source>.py
# then in config.json:  "source": { "adapter": "<source>" }
```

Read `ingest/README.md` for the contract and `ingest/schema.py` for the field
list. Use `ingest/parsers.py` rather than re-solving decimal commas and
duration notations.

## Path B — a single activity file (`data/activities/`)

### 1. Check whether a reader already claims that extension

```sh
python3 -m ingest --readers
ls data/activities/
```

`.fit` ships. If the user's file is `.fit` and still didn't read, it's a bug in
the reader or a genuinely odd file — decode it and look before assuming.

### 2. Write a reader

```sh
cp ingest/readers/_template.py ingest/readers/local/<format>.py
```

Declare `EXTENSIONS = (".tcx",)` and that's the wiring — there is **nothing to
set in config**. Return an `Activity` with `session`, `laps` and `records`; the
field list with units is in `ingest/activity.py`. Only `t` (seconds from the
session start) is required on a record; omit anything the format doesn't carry
— except laps, if the format has them, for the reason below.

```sh
python3 analyze.py <file> --dry-run      # runs it, writes nothing
```

`_template.py` raises deliberately, so replace that line. Prefer the standard
library: this repo has one dependency and adding a parsing package for one
format is a real cost — XML formats like `.tcx` and `.gpx` are
`xml.etree.ElementTree`.

## The things that actually go wrong

Shared by both paths:

1. **Units.** `duration_s` seconds, `sleep_min` minutes, `pace_s_per_km`
   seconds, record `t` seconds, `distance_km` in `config.source.distance_unit`,
   and in a reader: metres, m/s, mm, ms. A unit error yields plausible numbers
   that are wrong — the worst failure this system has. Check every numeric
   field against the raw file once, by eye. A 5 km run must not come out as
   5000; a 45-minute session is `duration_s: 2700`, not 45.
2. **`avg_hr` missing.** No average heart rate means no TRIMP, which means no
   load, which means an empty dashboard. If the source doesn't export it, say
   that plainly — it's a real limitation of their data, not something to paper
   over.
3. **Unstable or non-local timestamps.** `datetime` (adapter) and
   `start_local` (reader) are half the dedupe key into `data/sessions.csv`, and
   must be local wall clock, `YYYY-MM-DD HH:MM:SS`. A reader emitting UTC
   creates a *second* session for a workout the CSV already supplied, silently
   doubling that day's TRIMP. `build.py` prints both rows when it notices, but
   not writing the bug is better. Read the offset out of the file, never from
   the machine's timezone.
4. **Silent skips.** Use `report.skipped()` and `report.missing_columns()`. A
   row dropped without a line of output becomes a bug report three weeks later
   reading "the chart looks wrong".
5. **Activity types.** Keep the source's own vocabulary ('Ride', not
   'Cycling'), and match what the user's *other* path already produces — a
   reader saying "Run" where the CSV says "Running" is a duplicated session.
   Config matches types by substring, so afterwards check whether
   `config.mechanical.weights`, `config.primary.match` and
   `config.plan.kinds[*].match` still name types that exist in this data.
6. **A bulk row's one average per session can be a blend, not a truth.** A run
   with a warm-up or cooldown walk, or any session with a stop or a slower
   stretch, gets one plausible-looking number that's actually two (or more)
   different efforts averaged together — and it can read as a change in
   fitness or form that never happened. This is exactly what the single-session
   path and `analyze.py`'s `moving_*` fields exist to fix. If the user has both
   a bulk export and per-session files available for the same source, mention
   that pairing an adapter with a reader (Path B) is what makes the correction
   possible — not just a bulk adapter on its own.

Reader-specific:

6. **Cadence units.** Several formats store a runner's 170 spm as 85
   revolutions. Double it **in the reader**, so nothing downstream has to know
   which sport it's looking at. A session reading 85 looks like a form collapse
   rather than a bug.

7. **Dropped laps.** Easy to treat as optional detail — `_template.py` has
   them commented out — but `analyze.py` reads the lap pattern to tell an
   interval session from a steady one, which is what keeps the coach from
   grading a rep session against an easy-run HR cap. `total_distance` and
   `total_timer_time` per lap are all it needs. A reader that skips laps
   works, and then every interval session that source records comes back
   shapeless with nothing in the output explaining why — so if the format
   carries laps, pass them through, and if it genuinely doesn't, tell the
   user that's what they're giving up.

## Finishing

```sh
python3 -m ingest --check     # bulk path: coverage + sample record
python3 analyze.py --dry-run  # single-session path
python3 build.py              # write it through
```

Tell the user which route you took (config, adapter, or reader), what didn't
map and why, and any config keys they should now revisit. If you wrote
something that works, mention they can PR it into `ingest/adapters/` or
`ingest/readers/` so the next person with that device gets it for free.
