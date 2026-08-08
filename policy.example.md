# Coaching policy — v1 (YYYY-MM-DD)

Standing rules for generating training plans. This file is the constitution:
Claude reads it *before* `context.md` every session and applies it, rather than
forming a fresh opinion each time. Change it deliberately, bump the version,
note what changed and why in the changelog.

> **This is a template.** Copy it to `policy.md` and rewrite it for yourself.
> `policy.md` is gitignored, so your real profile stays on your machine. The
> numbers below are placeholders, not recommendations — and none of this is
> medical advice.

**Athlete:** `<injury or limiter, how long, current severity, and the specific
presentation — the distinguishing detail matters more than the label>`.
VO₂max `<n>`. HRV baseline `<lo>`–`<hi>` ms, resting HR `<n>`.

**Goal:** `<what you actually want, and what kind of athlete you are>`.

---

## 0. What the history actually says

Write this section *after* loading a long export — not from assumption. The point
is to name the real binding constraint, because the rest of the policy follows
from it. Useful questions:

- Is volume actually high, or is this a tissue-capacity problem at low volume?
- Is the pattern stop–start? How long are the gaps, and what precedes them?
- Did the last flare follow a rise in *distance* or a rise in *intensity*?
- What breaks mechanically — cadence, form at slow paces, terrain?

**State the binding constraints explicitly, in priority order.** Every later
section should be traceable to one of them.

---

## 1. Two load channels

Garmin blends everything into one training load, which is why it recommends hard
running when the aerobic system is fresh but the tibia is not.

| Channel | Measures | Target |
|---|---|---|
| **Aerobic** (TRIMP) | Cardiovascular work from *any* activity | Held steady — this is the fitness we protect |
| **Impact** (weighted km) | Mechanical bone/tendon load | Grown slowly and monotonically |

Impact weights: Running 1.0, Trail 1.15, Treadmill 0.85, Walking 0.25,
Hiking 0.30, cycling/swimming/strength **0.0**.

When the injured tissue needs relief, aerobic load moves to the bike — it does
not disappear.

### Account for the aerobic work you do anyway

Commuting, dog walks, whatever is already in your week — quantify it from the
logged activities rather than guessing, and decide whether planned cross-training
*replaces* it or *stacks on top of* it. Getting this wrong in either direction is
the most common way a plan drifts from reality.

---

## 2. The never-stop floor

**Minimum `<n>` runs per week, every week, unless a §4 red rule fires.**

If your history says "reduce" reliably decays into "stop," write the floor in
explicitly — stopping resets whatever adaptation consistent loading builds.
During a flare, volume drops and pace slows, but running does not reach zero.

Floor session: `<very short, very easy, target cadence>`.

The only thing that overrides this floor is a §4 red rule.

---

## 3. The 24-hour rule (the main decision procedure)

For chronic overuse injuries, symptom-free is the wrong target — *stable and
non-escalating* is the right one. After every run, judge against the morning:

| Next-morning symptom vs. before the run | Verdict |
|---|---|
| Same or better | ✅ Progress as planned |
| Slightly worse, settles by midday | ⏸️ Repeat the week, do not increase |
| Worse and still worse at 24 h | 🔻 Cut to floor sessions for a week |
| Worse *during* the run, run to run | 🛑 Red rule — see §4 |

During a run, pain up to **`<n>`/10 that stays flat is acceptable**. Pain that
climbs while running is not, ever.

---

## 4. Hard gates

**Red rules — these override the §2 floor:**
- Pain rising *during* a run → stop that run immediately, walk home
- `<presentation that means "stop self-managing" for your specific injury>` → §8
- Pain at rest or at night → §8
- Symptom ≥ `<n>`/10 → no impact 72 h

**Amber rules — modify the session, keep the week:**

| Signal | Action |
|---|---|
| Symptom ≥ `<n>`/10 today | Replace the run with equal-duration bike |
| HRV < `<lo>` ms **and** RHR ≥ `<n>` | Easy bike instead of the run |
| 3-day sleep debt > `<n>` h | Easy only, no quality session |
| Impact ACWR > 1.3 | Cut running 20% next week, hold pace |
| Aerobic ACWR > 1.5 | Cap weekly TRIMP at chronic × 1.2 |

One signal alone is never a veto — two must agree. A single sub-baseline HRV
night with normal RHR is noise.

---

## 5. Intensity discipline

- **At least 80% of runs at avg HR ≤ `<n>`.** Easy means easy
- **No run above avg HR `<n>`** until §7's benchmark is under `<pace>`
- If HR drifts above the cap mid-run, **walk until it drops**. Walking is not failure
- One quality session per week maximum, and only from Phase B onward

Anchor the caps to figures from your own export, not to a formula.

---

## 6. Cadence — the primary mechanical fix

**Target ≥ `<n>` spm on every run, at every pace.** Metronome at `<n>`.

The failure mode worth checking for: cadence collapsing on slow runs, meaning
slowing down is achieved by shuffling rather than by shortening stride. **Slow
down by shortening the stride, not by slowing the turnover.**

Expect pace to get slower while cadence rewires. That is the cost of the change,
not a regression.

A run averaging below `<n>` spm counts as a missed target and is logged as such.

---

## 7. The metric

Pick a metric that improves by training *correctly*, not by training *harder* —
otherwise the metric fights the policy. A 5k time is a number you improve by
running harder, which for an impact-limited runner is the exact behaviour that
causes the flare.

**Suggested headline metric: pace at a fixed easy HR** — aerobic efficiency. It
improves with consistency and cannot be gamed by pushing.

- **Current benchmark:** `<pace>` at HR `<n>`
- **Milestone:** `<pace>` → unlocks Phase B quality work
- **Target:** `<pace>`

**Time trials: at most every 8 weeks**, and only when symptoms have been ≤1 for
14 days. It is a measurement, not a workout.

---

## 8. Escalation — stop self-managing and see a professional if

- Pain localises to a **specific point** on the bone (smaller than a coin)
- Pain at **rest or at night**
- Pain **worsening week over week** despite reduced load
- Swelling, or pain that makes you limp

Record how your *current* presentation differs from this list, and recheck
monthly. This file is a training policy, not a diagnosis.

---

## 9. Phases

| | Entry | Running | Rules |
|---|---|---|---|
| **A — Rebuild** *(current)* | now | `<n>`×/wk, ≤ `<n>` km total, ≤ `<n>` km per run | All easy (§5), cadence target, 48 h between runs, flat |
| **B — Build** | benchmark ≤ `<pace>` **and** 4 weeks no flare | +10%/wk max, ≤ `<n>` km | One quality session/wk. Hills ≤ `<n>` m |
| **C — Perform** | benchmark ≤ `<pace>`, 8 weeks stable | ≤ `<n>` km | TT every 8 wks. Back-to-back days allowed, max 2 |

Any red rule drops you one phase. Amber rules repeat the current week.

**Progression is monotonic:** never increase volume and pace in the same week.
Never increase two weeks running without a flat week between.

---

## 10. Strength — the actual capacity fix

Running less buys time; strength is what raises the ceiling you keep hitting.
**3× per week, ~12 minutes**, on non-running days.

**There is one session, not a rotation** — the same exercises every time. Below
is a lower-leg/MTSS-oriented example; swap it for whatever your limiter needs.

| Exercise | Sets × reps | Note |
|---|---|---|
| Calf raise, straight knee | 3 × 15 | 3-second lower — the slow lower is the point |
| Calf raise, bent knee (~30°) | 3 × 15 | Soleus |
| Tibialis raise (toes up, back to wall) | 3 × 20 | The anterior shin itself |
| Single-leg balance | 2 × 45 s per side | Barefoot, eyes open; harder with eyes closed |
| Side-lying hip abduction | 2 × 15 per side | Hip stability feeds down the chain |

Two calf variations because the calf complex has two muscles with different jobs:
gastrocnemius crosses the knee so it loads straight-legged, soleus doesn't so it
only loads bent-knee — and soleus is the one that takes the running load.

Progress by **adding load before adding reps**. Log it as an activity; it counts
as a session.

---

## 11. Review cadence

Weekly, normally Sunday:

1. Export CSVs → `data/raw/` → `python3 build.py`
2. Claude interviews, appends to `journal.md`
3. Claude reads `policy.md` + `context.md` + `journal.md` → writes `plans/YYYY-MM-DD.md`

   **Every plan must contain a ```week block** — the dashboard reads it to render
   the day strip, and it is the single source of truth for the schedule. One line
   per day, seven days:

   ```
   YYYY-MM-DD | run|cross|strength|rest | Short title | Detail sentence
   ```

   The rationale prose goes around it, not inside it. Days tick off automatically
   as matching activities appear in the Garmin data.

4. Rules that felt wrong get changed *here* with a version bump — never improvised
   inside a single conversation

---

## Changelog

- **v1 (YYYY-MM-DD)** — Initial.
