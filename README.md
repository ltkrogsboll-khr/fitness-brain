# fitness-brain

A training system for HR-based endurance sport that you can actually argue with —
one you can tell how your body actually feels, and that holds the plan to rules
you wrote down instead of improvising a new opinion each session.

Local-only: a Python build step over your CSV exports, a single-file dashboard,
and a coach chat that reads your policy on every turn.

## Run it

```sh
cp policy.example.md policy.md           # the rules — rewrite for yourself
cp config.example.json config.json       # the numbers — or delete for neutral defaults
./run.sh                                 # → http://127.0.0.1:8765
./.venv/bin/python serve.py --set-key    # one-time: store Anthropic key in Keychain
```

Everything except the chat works without a key. `serve.py` resolves the key from
`ANTHROPIC_API_KEY`, then `.env`, then the macOS login Keychain — Keychain is
recommended, since this directory may sit in iCloud Drive where a plaintext `.env`
would sync off the machine.

## The two files that make it yours

**`policy.md`** is the constitution: phases, gates, caps, and the reasoning behind
them. The coach reads it before anything else, every turn, which is what keeps
answers consistent instead of re-derived per conversation.

**`config.json`** is the same rules in machine-readable form — the numbers the
dashboard enforces and the labels it prints. Keeping them in one file is what stops
the page and the policy from quoting different ceilings at you.

| Section | What it controls |
|---|---|
| `cycle` | How many days one training block is, and what you call it. See [Cycle length](#cycle-length) |
| `aerobic` | The TRIMP channel and its ACWR ceiling. Always on |
| `mechanical` | Optional second channel: per-activity-type weights for whatever tissue adapts slower than your heart. Off by default |
| `primary` | Which activity types count as headline volume, and any per-cycle cap |
| `form_metric` | Any per-session number with a target — cadence, stroke rate, power |
| `benchmark` | Pace at a fixed HR: the metric that can't be gamed by pushing |
| `readiness` | Sleep, RHR and HRV thresholds |
| `journal` | The fields you score yourself on |
| `plan` | Session kinds, and how each decides it was done |
| `source` | Everything about your CSV export — filenames, columns, date and duration formats, decimals, units. See [Using a different export](#using-a-different-export) |
| `coach` | What the coach calls itself, and its escalation rule |

Anything you leave out falls back to a neutral default, so a partial config is
fine. With no `config.json` at all you get a generic HR-based setup: one load
channel, no form metric, one soreness field.

## The loop

Run it whenever you plan — every Sunday, every ten days, or whenever the last
block ran out. Nothing schedules this for you.

1. Export from your data source (e.g. watch, website) into `data/raw/` — activities, sleep, and optionally HRV.
2. `python3 build.py` — ingests, dedupes, derives, writes `context.md`.
3. Ask the coach for the next block. It reads `policy.md` → `context.md` →
   `journal.md` and writes `plans/YYYY-MM-DD.md`.

The newest file in `plans/` carries a fenced ` ```cycle ` block, one line per day,
which drives the dashboard's day strip:

```
2026-08-09 | run | Run 2.5 km | Cadence calibration. Metronome 170. HR ≤140.
```

The block is read as the list of dates it contains — five days, ten, a fortnight,
starting on any weekday. Kinds come from `config.plan.kinds`. Completion is
inferred from what actually got logged, not ticked by hand. (` ```week ` is the
original fence name and still parses, so old plans keep rendering.)

## Cycle length

`config.cycle.days` is one training block, and it's a display and volume-window
setting rather than a schedule — the engine never asks what day of the week it is.

| It changes | It doesn't change |
|---|---|
| How wide the load-chart bars are, counted back from your newest day of data | **ACWR**, which stays on the standard 7:28-day rolling windows whatever you set |
| The window `primary.cap` is measured over | Which days you can plan, or how long a plan block may be |
| The noun the dashboard uses — `label` and `per`, both derived if you leave them blank | Anything in ingest, TRIMP, or the chronic baseline |

Bars are totalled over fixed-width blocks ending on your newest day, not over
calendar weeks. That drops the Monday start, and it means the last bar is a full
block rather than a partial week that reads as a sudden collapse in load. The
dashed chronic line is the 28-day average scaled to one bar's width, so
bar-against-line is the same comparison at any block length.

## Files

| Path | What it is |
|---|---|
| `build.py` | Ingest → dedupe → derive → emit. Stdlib only |
| `serve.py` | Local dashboard + chat proxy. Binds 127.0.0.1 only |
| `index.html` | The whole frontend. No build step, no CDN |
| `config.py` | Defaults, and the merge that makes `config.json` optional |
| `run.sh` | Starts the server |

Everything personal is gitignored and never leaves the machine: `data/`,
`config.json`, `policy.md`, the generated `context.md`, `journal.md`, `plans/`,
`chats/`.

## The one design decision worth knowing

Load is split into **two channels** instead of the single blended number a watch
gives you:

- **Aerobic (TRIMP)** — from any activity, including cycling and swimming
- **Mechanical (weighted km)** — scaled per activity type, zero for non-impact work

That separation is the point. It lets a plan hold aerobic fitness flat while
cutting tissue load, which a single load score cannot express — and is why a watch
keeps recommending hard sessions into a flare.

Sleep and HRV exports are typically capped at a rolling few weeks. `build.py`
upserts each export into `data/daily.csv`, so after a couple of months you hold
more history than the vendor will hand back, which is what makes a real 28-day
chronic baseline possible.

## Using a different export

Nothing about the engine assumes a particular vendor; `config.source` holds the
whole contract with your CSVs.

| Key | Assumption it removes |
|---|---|
| `files` | Which filename substring routes to activities / sleep / HRV |
| `*_columns` | Header names. `null` disables one; `date: null` means "first column" |
| `datetime_formats`, `date_formats` | `strptime` patterns, tried in order. Defaults are deliberately unambiguous — add the one format your export uses rather than both `%d/%m/%Y` and `%m/%d/%Y` |
| `duration_formats` | `hms` `00:44:47` · `hm` `6:24` · `hm_text` `6h 24min` · `minutes` · `seconds` |
| `decimal` | `auto` sniffs each file (`6,56` votes comma, `6.56` dot); force `comma`/`dot` if a file has too few fractional numbers to tell |
| `missing` | Cell values meaning "no reading" |
| `distance_unit` | `km` or `mi` — labels, and what `metre_distance_types` converts into |

**Read the ingest report.** `build.py` prints one line per file plus a `!` line for
anything it skipped, so a wrong mapping is a sentence rather than an empty chart:

```
Ingest
  Activities.csv  activities read   120  kept   120  skipped    0  comma-decimal
  Sleep.csv       sleep      read    28  kept    27  skipped    1  dot-decimal
  ! Body Composition.csv: matches no pattern in config.source.files — not read
  ! activity: no column named 'Average Heartrate' — fix or null out config.source.activity_columns
  ! activities: 3 rows skipped, unparsed timestamp '08/02/2026 06:00' — add its format to config.source.datetime_formats
```

The dashboard's **Rebuild from CSVs** shows the same report, but only when
something was skipped.

Two assumptions are still in code, and both are opt-in: year-less HRV dates
(`8 Aug`, English months, inferred by walking backwards from the newest row —
used only when the value doesn't match `date_formats`), and running/cycling
form fields like GCT balance.

If your source is far enough from a CSV export that none of this fits, the better
seam is `data/daily.csv` and `data/sessions.csv`. That schema is the real
interface: write those two files yourself and the dashboard, ACWR channels,
context and coach all work unchanged. (Their distance columns are named `_km`
whatever `distance_unit` says; the values are in your unit, the names are just
history.)

## Scope

Built for sports where heart rate is the load signal — running, cycling, swimming,
rowing. TRIMP needs an HR stream, so strength-only training has no aerobic load to
model.

**This is not medical advice.** It's a tool for organising your own training data
against rules you wrote yourself. Pain that is worsening, focal, or present at rest
belongs with a clinician, not a dashboard.

MIT licensed — see [LICENSE](LICENSE).
