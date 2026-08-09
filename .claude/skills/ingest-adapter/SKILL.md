---
name: ingest-adapter
description: Make this repo read the user's fitness export — Strava, Polar, Coros, Whoop, Suunto, Apple Health, Intervals.icu, a hand-rolled CSV. Use when build.py reports skipped rows or empty columns, when the dashboard is missing a number, or when the user says their export isn't Garmin's. Covers both the config-only path and writing an adapter in ingest/adapters/local/.
---

# Making this repo read someone else's export

Everyone drops exports in the same folder: `data/raw/`. What changes per data
source is the **adapter** that reads them — one Python file behind a fixed
contract. Everything downstream (TRIMP, ACWR, the CSVs, `context.md`, the
dashboard, the coach) reads only normalized records and must not be touched.

**Never edit `build.py`, `serve.py`, `index.html` or `config.py` for this.** If
a data-source problem seems to need a change there, the boundary is wrong —
say so instead of crossing it. The one exception is `config.json`, which is the
user's own untracked file.

## Work in this order

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

Then:

```sh
python3 -m ingest --check
```

This runs ingestion only — it writes nothing. It prints the ingest report, then
how many records carried each field. **Read the coverage table**: a field at
`0/120` is a mapping that didn't land. Iterate here until it looks right.

### 3. Write an adapter when the shape differs

Config can't fix: one row per lap, JSON/TCX/SQLite, several files per period,
sleep stages that need summing, a distance column carrying its own unit,
per-vendor activity-type quirks.

```sh
cp ingest/adapters/_template.py ingest/adapters/local/<source>.py
```

`ingest/adapters/local/` is the user's folder — upstream never writes there, so
their adapter survives every `git pull`. Name it after the source
(`strava.py`, `polar.py`). Then set in `config.json`:

```json
"source": { "adapter": "<source>" }
```

If a shipped adapter is *nearly* right, copy it to `local/` under **the same
name** — a local file shadows the shipped one, so the user gets to bend it
without editing a tracked file and without losing upstream's copy.

Read `ingest/README.md` for the contract and `ingest/schema.py` for the field
list. Use `ingest/parsers.py` rather than re-solving decimal commas and
duration notations; read it before writing your own parsing.

### 4. Verify, then write through

```sh
python3 -m ingest --check     # coverage table + most-complete sample record
python3 build.py              # writes data/*.csv and context.md
```

Sanity-check the sample record against the raw file with your own eyes: a
5 km run must not come out as 5000, a 45-minute session must be
`duration_s: 2700`, not 45.

## The five things that actually go wrong

1. **Units.** `duration_s` seconds, `sleep_min` minutes, `pace_s_per_km`
   seconds, `distance_km` in `config.source.distance_unit`. A unit error yields
   plausible numbers that are wrong — the worst failure this system has. Check
   every numeric field against the raw cell once.
2. **`avg_hr` missing.** No average heart rate means no TRIMP, which means no
   load, which means an empty dashboard. If the source doesn't export it, say
   that plainly to the user — it's a real limitation of their data, not
   something to paper over.
3. **Unstable `datetime`.** It is half the dedupe key. If it shifts between
   exports (timezone re-rendered, seconds dropped), every re-export duplicates
   every workout. Emit `YYYY-MM-DD HH:MM:SS` local time, always.
4. **Silent skips.** Use `report.skipped()` and `report.missing_columns()`.
   A row dropped without a line of output becomes a bug report three weeks
   later that reads "the chart looks wrong".
5. **Activity types.** Keep the source's own vocabulary ('Ride', not
   'Cycling'). Config matches types by substring, so afterwards check whether
   `config.mechanical.weights`, `config.primary.match` and
   `config.plan.kinds[*].match` still name types that exist in this data — and
   update them if not.

## Finishing

Tell the user which route you took (config vs. adapter), what didn't map and
why, and any config keys they should now revisit. If you wrote an adapter that
works, mention they can PR it into `ingest/adapters/` so the next person with
that device gets it for free.
