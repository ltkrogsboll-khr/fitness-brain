# Working in this repo

A local training system: read what a watch exported, accumulate it, and give a
coach enough grounded context to plan the next block. Python 3, **standard
library only** — `anthropic` is installed for the chat and is the sole
dependency. Keep it that way; if something seems to need a package, that is
worth raising rather than doing quietly.

## The one boundary that matters

```
data/raw/*         ->  [ adapter ]  ->  sessions / sleep / hrv  ->  [ engine ]
data/activities/*  ->  [ reader  ]  ->  Activity                    never
                        swappable        the contracts               changes
```

Everything upstream of the arrows is vendor-shaped and belongs in `ingest/`.
Everything downstream — TRIMP, ACWR, the accumulating CSVs, `context.md`, the
dashboard, the coach — reads only the normalized records and must not learn
what a Garmin is.

**So: a new data source is a new file in `ingest/`, never an edit to
`build.py`, `serve.py`, `index.html` or `config.py`.** If a data-source problem
looks like it needs a change in those, the boundary is being crossed — say so
instead. (`config.json` is the user's own untracked file and is fair game.)

The two paths are chosen differently, on purpose: a bulk export folder belongs
to one vendor, so `config.source.adapter` names the adapter; a single-session
file is self-describing, so readers are claimed by file extension and picked
per file.

## Layout

| Path | What it is |
|---|---|
| `ingest/schema.py` | Contract for the bulk path: sessions/sleep/hrv, with units |
| `ingest/activity.py` | Contract for the single-session path, plus the reader registry |
| `ingest/adapters/`, `ingest/readers/` | Shipped. `local/` under each is the user's — upstream never writes there |
| `ingest/parsers.py` | Decimal commas, five duration notations, timezone-dropping timestamps. Read it before writing your own parsing |
| `build.py` | Dedupe → derive → emit. Owns TRIMP, ACWR and the upsert |
| `analyze.py` | One session at full resolution → session fields + a journal line |
| `serve.py` | Dashboard, chat proxy, and the coach's system prompt |
| `config.py` | `DEFAULTS`, merged under the user's `config.json` |

## Units are the contract

A wrong unit produces plausible numbers instead of an error, which is the worst
failure this repo has. `duration_s` seconds, `sleep_min` minutes,
`pace_s_per_km` seconds, record `t` seconds from start, cadence in the sport's
own unit (steps/min on foot — several formats store a runner's 170 as 85 and
expect the reader to double it). Check any numeric field against the raw file
once, by eye.

Two more that fail silently:

- **`datetime` / `start_local` is local wall clock**, and half the dedupe key
  into `data/sessions.csv`. If it shifts between exports, or a reader emits
  UTC, every workout lands twice and its load counts twice.
- **`avg_hr` missing means no TRIMP**, which means no load, which means an
  empty dashboard. If a source genuinely doesn't export it, say so plainly —
  that's a real limitation of the data, not something to paper over.

## Report, don't swallow

A skipped row with no output becomes "the chart looks wrong" three weeks later.
`ingest/report.py` exists for this: name the file, the count, and the config key
that would have fixed it. Same instinct in `build.py` — it prints both rows when
it sees one workout counted twice, rather than letting the load quietly double.

## Verifying a change

```sh
python3 -m ingest              # adapters, and which is active
python3 -m ingest --readers    # single-session readers and their extensions
python3 -m ingest --check      # run ingestion, write nothing, show coverage
python3 analyze.py --dry-run   # newest activity file, write nothing
python3 build.py               # writes data/*.csv and context.md
./run.sh                       # build, then serve on 127.0.0.1:8765
```

`--check` and `--dry-run` write nothing and are the loop to work in. `build.py`
is idempotent — both CSV upserts and the journal line are keyed, so re-running
is safe.

To test the write path without touching real data, copy the repo to a scratch
dir and run there; paths are all derived from the file's own location.

## Personal data

`data/`, `config.json`, `policy.md`, `journal.md`, `context.md`, `plans/` and
`chats/` are gitignored and stay on the machine. Don't commit them, don't paste
their contents into anything that leaves the box, and don't add fixtures drawn
from them. `config.example.json` and `policy.example.md` are the tracked,
shareable versions.

## Prose style

The comments here explain *why*, not what — a reader can see what. Docstrings
carry the reasoning behind a design decision, and the failure it prevents.
Match that when editing; it is the house style, not decoration.
