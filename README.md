# fitness-brain

A shin-aware training system that replaces Garmin's coach with something that can
actually be told "my shins hurt this week."

Local-only: a Python build step over Garmin CSV exports, a single-file dashboard,
and a coach chat that reads your written policy on every turn.

## Run it

```sh
cp policy.example.md policy.md           # then rewrite it for yourself
./run.sh                                 # → http://127.0.0.1:8765
./.venv/bin/python serve.py --set-key    # one-time: store Anthropic key in Keychain
```

`policy.md` is the constitution — the coach reads it before anything else, every
turn, which is what keeps answers consistent with written rules instead of
improvised per conversation. The example is a skeleton with placeholder numbers;
fill it in from your own history, not from the defaults.

Everything except the chat works without a key. `serve.py` resolves the key from
`ANTHROPIC_API_KEY`, then `.env`, then the macOS login Keychain — Keychain is
recommended, since this directory may sit in iCloud Drive where a plaintext `.env`
would sync off the machine.

## Weekly loop

1. Export from Garmin Connect into `data/raw/`: `Activities.csv`, `Sleep.csv`,
   and optionally `HRV Status.csv`.
2. `python3 build.py` — ingests, dedupes, derives, writes `context.md`.
3. Ask the coach for the week. It reads `policy.md` → `context.md` → `journal.md`
   and writes `plans/YYYY-MM-DD.md`.

The newest file in `plans/` carries a fenced ` ```week ` block, one line per day,
which drives the dashboard's week strip:

```
2026-08-09 | run | Run 2.5 km | Cadence calibration. Metronome 170. HR ≤140.
```

Kinds are `run`, `cross`, `strength`, `rest`. Completion is inferred from what
actually got logged, not ticked by hand.

## Files

| Path | What it is |
|---|---|
| `build.py` | Ingest → dedupe → derive → emit. Stdlib only |
| `serve.py` | Local dashboard + chat proxy. Binds 127.0.0.1 only |
| `index.html` | The whole frontend. No build step, no CDN |
| `run.sh` | Starts the server |
| `policy.example.md` | Template for `policy.md` — phases, gates, caps |

Everything personal is gitignored and never leaves the machine: `data/`,
`policy.md`, the generated `context.md`, `journal.md`, `plans/`, `chats/`.

## The one design decision worth knowing

Load is split into **two channels** instead of Garmin's single blended number:

- **Aerobic (TRIMP)** — from any activity, including cycling and swimming
- **Impact (weighted km)** — running counts fully, walking a quarter, cycling zero

That separation is the point. It lets a plan hold aerobic fitness flat while
cutting bone load, which a single load score cannot express — and is why Garmin
keeps recommending hard runs into a shin flare.

Garmin's Sleep and HRV exports are capped at a rolling 4 weeks. `build.py` upserts
each export into `data/daily.csv`, so after a couple of months you hold more
history than Garmin will hand back, which is what makes a real 28-day chronic
baseline possible.
