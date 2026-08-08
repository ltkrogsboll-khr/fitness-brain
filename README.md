# fitness-brain

A training system for HR-based endurance sport that you can actually argue with —
one you can tell how your body feels this week, and that holds the plan to rules
you wrote down instead of improvising a new opinion each session.

Local-only: a Python build step over your watch's CSV exports, a single-file
dashboard, and a coach chat that reads your policy on every turn.

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
| `aerobic` | The TRIMP channel and its ACWR ceiling. Always on |
| `mechanical` | Optional second channel: per-activity-type weights for whatever tissue adapts slower than your heart. Off by default |
| `primary` | Which activity types count as headline volume, and any weekly cap |
| `form_metric` | Any per-session number with a target — cadence, stroke rate, power |
| `benchmark` | Pace at a fixed HR: the metric that can't be gamed by pushing |
| `readiness` | Sleep, RHR and HRV thresholds |
| `journal` | The fields you score yourself on |
| `plan` | Session kinds, and how each decides it was done |
| `source` | Filename patterns and CSV column names for your export |
| `coach` | What the coach calls itself, and its escalation rule |

Anything you leave out falls back to a neutral default, so a partial config is
fine. With no `config.json` at all you get a generic HR-based setup: one load
channel, no form metric, one soreness field.

## Weekly loop

1. Export from your watch into `data/raw/` — activities, sleep, and optionally HRV.
2. `python3 build.py` — ingests, dedupes, derives, writes `context.md`.
3. Ask the coach for the week. It reads `policy.md` → `context.md` → `journal.md`
   and writes `plans/YYYY-MM-DD.md`.

The newest file in `plans/` carries a fenced ` ```week ` block, one line per day,
which drives the dashboard's week strip:

```
2026-08-09 | run | Run 2.5 km | Cadence calibration. Metronome 170. HR ≤140.
```

Kinds come from `config.plan.kinds`. Completion is inferred from what actually got
logged, not ticked by hand.

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

## Scope

Built for sports where heart rate is the load signal — running, cycling, swimming,
rowing. TRIMP needs an HR stream, so strength-only training has no aerobic load to
model. Not medical advice.
