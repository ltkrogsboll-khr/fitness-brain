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
# pick a coach LLM in config.json (see "Choosing the coach's LLM" below) —
# nothing is assumed for you
./run.sh                                 # → http://127.0.0.1:8765
./.venv/bin/python serve.py --set-key    # one-time: store your LLM's API key in Keychain
```

Everything except the chat works without a key or a chosen LLM. Once you've
set `config.coach.llm`, `serve.py` resolves the key from that provider's env
var, then `.env`, then the macOS login Keychain — Keychain is recommended,
since this directory may sit in iCloud Drive where a plaintext `.env` would
sync off the machine. See
[Choosing the coach's LLM](#choosing-the-coachs-llm).

**About your data source:** the shipped adapter reads Garmin-shaped CSV, and
config alone covers most other CSV exporters too — but if yours is shaped
differently (JSON, TCX, one row per lap), you'll likely need to write a small
adapter of your own. That's expected, not a bug report — see
[Using a different export](#using-a-different-export), and consider PRing it
back once it works.

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
| `source` | Which ingest adapter reads `data/raw/`, and everything it needs to know — filenames, columns, date and duration formats, decimals, units. See [Using a different export](#using-a-different-export) |
| `coach` | What the coach calls itself, its escalation rule, and which LLM answers. See [Choosing the coach's LLM](#choosing-the-coachs-llm) |

Anything you leave out falls back to a neutral default, so a partial config is
fine. With no `config.json` at all you get a generic HR-based setup: one load
channel, no form metric, one soreness field.

## Choosing the coach's LLM

The chat isn't wired to one vendor — and unlike most of `config.json`, this
section has no default at all. Nothing ships preferring one LLM over another;
until you set `provider` and `model`, chat stays disabled (everything else
still works). `serve.py` talks to whatever `config.coach.llm` points at over
plain HTTP (`llm.py`) — no SDK, so there's nothing to install per provider.
Four keys:

| Key | Required | What it does |
|---|---|---|
| `provider` | yes | Which request/response *shape* to speak: `"anthropic"` (the Messages API) or `"openai"` (the Chat Completions API — also what Ollama, LM Studio and most gateways speak) |
| `model` | yes | The model name to send |
| `base_url` | no — defaults to the shape's own API | Where to send it. Set this to point at OpenAI, a gateway, or a local server |
| `api_key_env` | no — defaults to the shape's own env var | Which environment variable `--set-key` and `serve.py` read the key from |

For Anthropic:

```json
"coach": {
  "llm": { "provider": "anthropic", "model": "claude-opus-5" }
}
```

Or OpenAI:

```json
"coach": {
  "llm": { "provider": "openai", "model": "gpt-5" }
}
```

Or point at a model running on your own machine — Ollama speaks the same
shape as OpenAI's API:

```json
"coach": {
  "llm": { "provider": "openai", "model": "llama3.1",
           "base_url": "http://localhost:11434/v1", "api_key_env": "OLLAMA_KEY" }
}
```

Ollama doesn't check the key at all, so any placeholder in `.env` works —
`echo 'OLLAMA_KEY=local' > .env` is enough. `serve.py --set-key` stores a key
per env var name, so switching providers back and forth doesn't overwrite one
you've already saved.

## The loop

Run it whenever you plan — every Sunday, every ten days, or whenever the last
block ran out. Nothing schedules this for you.

1. Export from your data source (e.g. watch, website) into `data/raw/` — activities, sleep, and optionally HRV. Same folder whatever the vendor; see [Using a different export](#using-a-different-export).
2. Optionally drop single-activity files (`.fit`) into `data/activities/` — see
   [One session, close up](#one-session-close-up).
3. `python3 build.py` — ingests, dedupes, derives, writes `context.md`.
4. Ask the coach for the next block. It reads `policy.md` → `context.md` →
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

Each kind says what counts as evidence, via `complete_when`:

| `complete_when` | A day is done when |
|---|---|
| `primary_volume` | Any distance in your main sport landed |
| `any_activity` | Anything at all landed |
| `activity_matches` | A logged activity's name contains one of `match` |
| `untracked` | Never asked — no export will ever mention it |
| `always` | Always — there was nothing to do |

`untracked` is the one worth knowing about. A 12-minute floor set or a bike
commute you never log would otherwise go red on the strip every week, and a
reminder that reads as a failure gets deleted. Those days stay in the plan and
are simply left alone: no tick, no **missed**, and out of the session count,
which then speaks only for days the data can actually speak for. The coach is
told the kind is untracked too, so it plans them without chasing you for a file.

## Cycle length

`config.cycle.days` is one training block — a display and volume-window
setting, not a schedule; the engine never asks what day of the week it is.
It sets how wide the load-chart bars are and the window `primary.cap` is
measured over, both counted back from your newest day of data rather than
calendar weeks — so the last bar is always a full block, not a partial week
that reads as a sudden collapse in load. It does **not** change ACWR, which
stays on the standard 7:28-day rolling windows whatever you set, or anything
in ingest, TRIMP, or the chronic baseline. The dashed chronic line is the
28-day average scaled to one bar's width, so bar-against-line stays the same
comparison at any block length.

## One session, close up

Drop a `.fit` file in `data/activities/` and rebuild. That's it — `build.py`
reads the folder, so the dashboard's **Rebuild** button is the whole workflow.

The folder is named for what the files are rather than who wrote them: Garmin,
Wahoo, Coros and Suunto all export `.fit`, and `.fit` ships (stdlib, no
dependency). Support for another format is a **reader** — the single-session
sibling of an adapter, claimed by file extension rather than config, so a
`.fit` and a `.tcx` can sit in the folder together and both just work. Same
swappable contract, same `local/` folder upstream never touches, same "write
your own and PR it" story as adapters — see
[Using a different export](#using-a-different-export). Anything unreadable in
the folder gets named in the ingest report rather than silently skipped;
`python3 -m ingest --readers` lists what's installed.

**Why bother, when the CSV already has cadence and stride?** Because a CSV row
is one average per session, and an average taken over a run that ends in a walk
home describes neither. A real example — the same 2.8 km run, both numbers true:

| | Session average (CSV) | While actually running (`.fit`) |
|---|---|---|
| Cadence | 154 spm | **171 spm**, 99% of it inside 165–175 |

The first says you missed a 170 target by 16. The second says you hit it. The
difference is a 3½-minute cooldown walk, and only one of those sentences should
reach a coaching conversation.

What it derives, per session, beyond what any summary row can hold: `moving_*`
cadence/HR/pace/distance with walking excluded, `pct_above_hr_cap`,
`cadence_in_band_pct`, `hr_drift_bpm` / `decoupling_pct` (whether HR rose
because you tired or because you sped up — a distinction a mean destroys),
`walk_break_s`, and `session_shape` — whether the lap pattern says intervals
or a steady effort, so a rep session reaches the coach named as one rather
than as an easy run you ran far too hard. (Empty when the laps can't say: a
ride logged as a single lap gets no shape rather than a guessed one.) The
numbers land as extra columns on the session row in `data/sessions.csv` — any
of them can become `config.form_metric.field`, and
`build.py`'s TRIMP/mechanical-km calculations prefer the `moving_*` versions
over the vendor's whole-activity totals whenever a session has them, so a
walked warm-up doesn't inflate load either. The prose becomes one line in
`journal.md`, so a dropped file reaches the next coaching conversation without
anyone typing it. The per-second streams themselves are never stored — nothing
downstream consumes 1 Hz data, and the file is still on disk if a new question
needs asking of it later.

Worth knowing before trusting a trend built only on the CSV: a swing in the
session-average cadence, pace, or HR across a stretch of your history can be a
swing in how much of each session was spent walking, not a change in fitness
or form. `context.md` states how many sessions carry the `.fit` correction and
how many don't, precisely so a real pattern isn't confused with a warm-up
ratio that happened to vary — read a surprising CSV-only trend with that in
mind before treating it as a diagnosis.

Targets live in `config.analysis` (`hr_cap`, `cadence_target`, `cadence_band`)
because they're what the *current plan* asked for — update them when the plan
changes. Shape detection deliberately has no such knobs: it reads the lap
pattern your watch already recorded, and a threshold you can tune is a
threshold that can be tuned into seeing intervals that weren't there. For a
one-off, `python3 analyze.py` takes the newest file in the folder and prints a
lap-by-lap report without writing anything:

```sh
python3 analyze.py --dry-run              # newest activity, print only
python3 analyze.py run.fit --hr-cap 145   # against a different ceiling
python3 analyze.py --all                  # backfill the whole folder
```

**The one thing to watch.** A `.fit` file and a CSV export of the same workout
must produce the same `(datetime, type)` key or the session lands twice — and
two sessions means double TRIMP, a wrong number that looks entirely plausible.
This normally just works; when it doesn't, `build.py` prints both rows and
says so rather than letting the load quietly double.

## Files

| Path | What it is |
|---|---|
| `ingest/` | The only code that knows what your export looks like. Swappable adapters behind a fixed contract — see [Using a different export](#using-a-different-export) |
| `build.py` | Dedupe → derive → emit, over whatever `ingest/` returns. Stdlib only |
| `analyze.py` | One activity, second by second, from its `.fit` file |
| `serve.py` | Local dashboard + chat proxy. Binds 127.0.0.1 only |
| `llm.py` | Raw-HTTP client for the coach's LLM — see [Choosing the coach's LLM](#choosing-the-coachs-llm) |
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

Everyone drops exports in the same place — `data/raw/`. What differs per vendor
is the **adapter** that reads them, and nothing downstream of it knows what a
watch is:

```
data/raw/*  →  [ adapter ]  →  sessions / sleep / hrv  →  [ the engine ]
                swappable       fixed contract            never changes
```

Shipped coverage (Garmin-shaped CSV, `.fit`) is necessarily partial — there are
far more watches and apps than shipped adapters, so not finding yours is the
normal case. Two routes, and the cheap one covers a lot of ground:

**Route 1 — config only, no code.** The default adapter (`garmin_csv`) is
column-mapped rather than Garmin-specific: if your export is one CSV per kind
with one row per workout, point `config.source` at your header names (column
names, date/duration formats, decimal style, units) and you're done. Run
`python3 -m ingest --check` to see field coverage without writing anything —
a column that mapped to nothing shows up as `0/120` instead of as a puzzling
chart a week later.

**Route 2 — write an adapter.** Config can't fix a different *shape*: one row
per lap, JSON or TCX, several files per period, sleep stages that need
summing. That's one Python file, and it's the expected path for a data source
this repo hasn't seen before:

```sh
cp ingest/adapters/_template.py ingest/adapters/local/mysource.py
#   ...then in config.json:  "source": { "adapter": "mysource" }
python3 -m ingest --check
```

`ingest/adapters/local/` is **yours** — no upstream commit ever writes there,
so it survives every `git pull`. `ingest/README.md` is the full contract
(units, required fields, what's optional); `.claude/skills/ingest-adapter/`
is a skill for doing this with an agent — "make this read my Strava export"
is usually the whole prompt. A single-session reader (for `.fit`-like files)
works the same way under `ingest/readers/local/`.

**If you write one, send it back as a PR.** The adapter contract keeps
vendor-specific code isolated to one file in `ingest/`, so contributing yours
back doesn't touch anything you'd want to keep private — and the next person
with your watch gets it for free instead of writing the same file again.

## Scope

Built for sports where heart rate is the load signal — running, cycling, swimming,
rowing. TRIMP needs an HR stream, so strength-only training has no aerobic load to
model.

**This is not medical advice.** It's a tool for organising your own training data
against rules you wrote yourself, using an LLM you choose and configure yourself
— it can give wrong or incomplete advice, same as any LLM. Pain that is worsening,
focal, or present at rest, or any other concerning symptom, belongs with a
clinician — not a dashboard, and not a chat with the coach.

MIT licensed — see [LICENSE](LICENSE).
