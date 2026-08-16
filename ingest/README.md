# Ingest

Two paths in, both swappable, both ending in records the engine already knows.

```
data/raw/*         ->  [ adapter ]  ->  sessions / sleep / hrv  ->  [ engine ]
one bulk export        swappable        the contract, schema.py     never
of everything                                                       changes

data/activities/*  ->  [ reader ]   ->  Activity  ->  [ analyze.py ]
one file, one          swappable        the contract,
session, per second                     activity.py
```

The **adapter** path is the history: weeks of workouts, sleep and HRV in one
export, one row per session. The **reader** path is one session in full detail,
sampled second by second — which is the only way to know that a run averaging
154 spm was actually held at 171 with a walk home on the end.

They're chosen differently, on purpose. A bulk export folder belongs to one
vendor, so `config.source.adapter` names the adapter. A single-session file is
self-describing, so readers are claimed by **file extension** and picked per
file — a `.fit` and a `.tcx` in the same folder both just work.

Everyone drops their exports in the same place: `data/raw/`. What differs
between a Garmin user and a Strava user is the adapter that reads them —
one file, selected by one config key.

Downstream of the arrow, nothing knows what a "watch" is. TRIMP, ACWR, the
accumulating CSVs, `context.md`, the dashboard and the coach read only the
records described in `schema.py`, so a new data source needs no edit anywhere
outside this folder.

## Using a different source

1. `python3 -m ingest` — see what's available.
2. If your export is a CSV with different column names, you don't need an
   adapter at all: point `config.source.activity_columns` (and the sleep/HRV
   ones) at your header names and add your timestamp format to
   `datetime_formats`. `garmin_csv` is column-mapped, not Garmin-specific.
3. If the *shape* differs — one row per lap, JSON, several files per week,
   sleep stages that need summing — write an adapter.

Ask your agent: the repo ships a skill for this at
`.claude/skills/ingest-adapter/`. Or by hand:

```sh
cp ingest/adapters/_template.py ingest/adapters/local/mysource.py
# edit, then in config.json:  "source": { "adapter": "mysource" }
python3 -m ingest --check      # runs it, writes nothing, reports field coverage
python3 build.py               # for real
```

## Reading a single-session format

Same idea, no config step — a reader declares the extensions it claims and is
picked per file:

```sh
python3 -m ingest --readers    # what's installed, and what each one claims
cp ingest/readers/_template.py ingest/readers/local/tcx.py
python3 analyze.py somefile.tcx --dry-run    # runs it, writes nothing
python3 build.py                             # for real
```

```python
CONTRACT = 1
DESCRIPTION = "Garmin TCX"
EXTENSIONS = (".tcx",)

def read(path, cfg=None) -> Activity
```

`Activity` holds `session`, `laps` and `records` — one dict per sample, `t` in
seconds from the start. The field list with units is in `activity.py`. Two
things are worth getting right on the first try, because neither errors:

- **`start_local` is local wall clock.** It's half the key into
  `data/sessions.csv`, so a UTC timestamp creates a *second* session for a
  workout you already have and doubles that day's load. `build.py` prints both
  rows when it spots this, but not writing the bug is better.
- **Cadence is in the sport's own unit** — steps per minute on foot. Several
  formats store a runner's 170 spm as 85 revolutions; double it in the reader,
  so nothing downstream has to know which sport it's looking at.

And one worth the extra half hour: **pass the laps through** if the format has
them. `analyze.py` reads their pattern to tell an interval session from a
steady one — `total_distance` and `total_timer_time` per lap is all it needs —
and that's what stops the coach grading a rep session against an easy-run HR
cap. Skipping them costs no error and no warning, just every interval session
that source will ever record coming back shapeless.

## The contract

An adapter is one file exposing one function:

```python
CONTRACT = 1
DESCRIPTION = "Polar Flow CSV export"

def ingest(raw_dir, cfg, report) -> Ingested
```

It returns `Ingested(sessions=[...], sleep={...}, hrv={...})`:

| | shape | key | required fields |
|---|---|---|---|
| `sessions` | list of dicts | `(datetime, type)` | `datetime`, `date`, `type` |
| `sleep` | dict `date -> dict` | `date` | `date` |
| `hrv` | dict `date -> dict` | `date` | `date` |

Rules worth knowing before you write one:

- **Everything else is optional.** No HRV export? Return `{}`. No cadence?
  Leave the field out. `None` means "no reading" and the engine handles it
  everywhere — a missing signal degrades a chart, it doesn't break a run.
- **Units are the contract**, not just names. `duration_s` is seconds,
  `sleep_min` is minutes, `pace_s_per_km` is seconds. Getting one wrong
  produces plausible wrong numbers instead of an error, which is the worst
  kind of bug this system can have. `schema.py` states the unit per field.
- **`datetime` must be stable across re-exports.** It's half the dedupe key,
  so the same workout must serialize identically every time you export.
- **Extra session fields are kept** and flow into `data/sessions.csv`, so an
  adapter can surface power or stroke rate and you can then set
  `config.form_metric.field` to it. Extra *sleep*/*hrv* fields are dropped —
  the daily table has fixed columns — and `validate()` says so out loud.
- **Report, don't swallow.** Every skipped row should produce a line naming
  what was skipped and what would fix it. `report.skipped()` and
  `report.missing_columns()` exist for exactly this.
- **`cfg["source"]["options"]`** is yours. Nothing in this repo reads it, so
  an adapter can take settings without patching `config.py`.

`schema.validate()` runs on whatever you return: it repairs what it can
(string numbers, `T` separators, a missing `date` derivable from `datetime`),
drops what it can't use, and reports both. So a rough adapter fails loudly
rather than writing junk into the database.

## Files

| Path | What it is |
|---|---|
| `schema.py` | The record shapes, with units. The contract itself |
| `parsers.py` | CSV reading, decimal-comma sniffing, five duration notations, timezone-dropping timestamps |
| `activity.py` | The *other* contract: one session at full resolution, plus the reader registry |
| `readers/` | Shipped single-session readers. Upstream owns these |
| `readers/local/` | Yours. Upstream never writes here |
| `report.py` | What ingest tells you it did |
| `adapters/` | Shipped adapters. Upstream owns these |
| `adapters/local/` | Yours. Upstream never writes here |
| `__main__.py` | `python3 -m ingest [--check]` |

## Contract versioning

`schema.CONTRACT` is bumped only when a change here could break an adapter
that lives outside this repo — a field renamed, a unit changed, a key made
required. Adding an optional field doesn't count.

Your adapter declares the version it was written against, so a pull that
changes the contract prints a warning naming your adapter instead of quietly
producing wrong numbers. That's what makes it safe to keep updating the rest
of the repo while holding your own ingestion code.
