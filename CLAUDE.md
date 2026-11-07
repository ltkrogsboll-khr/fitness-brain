# Working in this repo

A local training system: read what a watch exported, accumulate it, and give a
coach enough grounded context to plan the next block. Python 3, **standard
library only, zero dependencies** — the chat talks to whatever LLM provider
`config.coach.llm` points at over raw HTTP (`llm.py`), not a vendor SDK. Keep
it that way; if something seems to need a package, that is worth raising
rather than doing quietly.

## The one boundary that matters

```
data/inbox/*.csv  ->  [ adapter ]  ->  sessions / sleep / hrv  ->  [ engine ]
data/inbox/*.fit  ->  [ reader  ]  ->  Activity                    never
                       swappable        the contracts               changes
```

One drop folder for both: a reader claims its files by extension, the adapter
declares `EXTENSIONS` for the rest (`.csv` if it doesn't say), and a file
neither claims is reported by name. `data/raw/` and `data/activities/` are the
old two-folder layout — not read any more, and `build.py` warns about any file
still left in one.

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
| `llm.py` | Raw-HTTP client for the coach's LLM — the Anthropic and OpenAI-compatible request/response shapes, and provider defaults |
| `config.py` | `DEFAULTS`, merged under the user's `config.json` |

## Units are the contract

A wrong unit produces plausible numbers instead of an error, which is the worst
failure this repo has. `duration_s` seconds, `sleep_min` minutes,
`pace_s_per_km` seconds, record `t` seconds from start, cadence in the sport's
own unit (steps/min on foot — several formats store a runner's 170 as 85 and
expect the reader to double it). Check any numeric field against the raw file
once, by eye.

A few more that fail silently:

- **`datetime` / `start_local` is local wall clock**, and half the dedupe key
  into `data/sessions.csv`. If it shifts between exports, or a reader emits
  UTC, every workout lands twice and its load counts twice.
- **`avg_hr` missing means no TRIMP**, which means no load, which means an
  empty dashboard. If a source genuinely doesn't export it, say so plainly —
  that's a real limitation of the data, not something to paper over.
- **A bulk export's one row per session is a plausible number, not a true
  one**, whenever a session mixes intensities — a warm-up or cooldown walk
  blended into a run's average cadence/pace/HR is the common case, but any
  session with a rest, a stop, or a slower stretch has the same problem. It
  reads as real data and can look like a change in fitness or form that never
  happened. `analyze.py`'s `moving_*` fields exist to correct exactly this
  when a `.fit` file backs the session — that's the reason the single-session
  path exists at all, not just for extra metrics.

## Report, don't swallow

A skipped row with no output becomes "the chart looks wrong" three weeks later.
`ingest/report.py` exists for this: name the file, the count, and the config key
that would have fixed it. Same instinct in `build.py` — it prints both rows when
it sees one workout counted twice, rather than letting the load quietly double.

## The inbox is a queue

After a successful build, `build.py` moves every file it took rows from into
`data/archive/<date>/` (`--keep-inbox` skips this). Byte-identical copies of a
moved file (Finder's `x.fit 2`) move with it as duplicates. Files that were
read but kept nothing, and files nothing reads, stay in the inbox so their
warning keeps showing.

The reason is merge order. Bulk exports of the same kind in the inbox merge by
filename sort order (`ingest/parsers.py:route_files`), not recency, and a
numbered re-export (`Sleep-2.csv`) sorts *before* the original, so a stale
file silently wins an overlap. One export at a time leaves the upsert as the
only merge, and there the newest build wins.

Two consequences to keep in mind:

- **Anything derived across builds must come from `data/*.csv`, not the
  inbox.** `hr_constants()` is the example: HR_rest and HR_max from the inbox
  alone would rescale TRIMP to whatever one export happened to contain.
  Likewise, anything `context.md` renders must look the same whether a row was
  computed this build or read back from the CSV (`cell()`).
- **Moving an archived file back re-reads it**, safely (the upsert is keyed),
  but its values then beat anything newer for the dates it covers. That's for
  repairing a bad build, and worth saying plainly to the user when you do it.
  `analyze.py` with no argument falls back to the newest archived file.

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
