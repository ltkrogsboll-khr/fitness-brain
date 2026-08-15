#!/usr/bin/env python3
"""
Local dashboard + coaching chat.

    ./.venv/bin/python serve.py      then open http://127.0.0.1:8765

Endpoints
    GET  /                 dashboard
    GET  /api/data         daily + sessions + derived series as JSON
    POST /api/journal      append a line to journal.md
    POST /api/build        re-run build.py
    POST /api/chat         SSE stream from Claude, with policy+context injected

Binds to 127.0.0.1 only. Needs ANTHROPIC_API_KEY in the environment for chat;
everything else works without it.
"""

from __future__ import annotations

import csv
import json
import os
import re
import subprocess
import sys
import datetime as dt
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import config

ROOT = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("PORT", "8765"))
MODEL = "claude-opus-5"
KEYCHAIN_SERVICE = "anthropic-api-key"

CFG = config.load()


def resolve_api_key():
    """Find the API key without needing an export in every shell.

    Order: environment -> .env -> macOS Keychain.

    Keychain is the default we recommend because this project directory is
    typically inside iCloud Drive; a plaintext .env there would be uploaded to
    Apple and synced to every device. The Keychain stays local to this Mac.
    """
    k = os.environ.get("ANTHROPIC_API_KEY")
    if k:
        return k, "environment"

    envfile = os.path.join(ROOT, ".env")
    if os.path.exists(envfile):
        for line in open(envfile, encoding="utf-8"):
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            name, _, val = line.partition("=")
            if name.strip() == "ANTHROPIC_API_KEY":
                val = val.strip().strip('"').strip("'")
                if val:
                    os.environ["ANTHROPIC_API_KEY"] = val
                    return val, ".env"

    if sys.platform == "darwin":
        try:
            r = subprocess.run(
                ["security", "find-generic-password",
                 "-a", os.environ.get("USER", ""), "-s", KEYCHAIN_SERVICE, "-w"],
                capture_output=True, text=True, timeout=10)
            if r.returncode == 0 and r.stdout.strip():
                val = r.stdout.strip()
                os.environ["ANTHROPIC_API_KEY"] = val
                return val, "macOS Keychain"
        except (OSError, subprocess.SubprocessError):
            pass

    return None, None


def store_key_in_keychain():
    """`python serve.py --set-key` -- prompt once, store in the login Keychain."""
    import getpass
    if sys.platform != "darwin":
        print("Keychain storage is macOS-only. Use a .env file instead:")
        print(f"  echo 'ANTHROPIC_API_KEY=sk-ant-...' > {os.path.join(ROOT, '.env')}")
        return 1
    key = getpass.getpass("Paste your Anthropic API key (hidden): ")
    # Hidden input makes mistakes invisible, so clean and check before storing.
    key = "".join(key.split())  # strip spaces/newlines anywhere
    if not key:
        print("Nothing entered, aborting.")
        return 1
    # Double-paste is the classic hidden-input error: same key twice, end to end.
    half = len(key) // 2
    if len(key) % 2 == 0 and key[:half] == key[half:]:
        key = key[:half]
        print("Noticed the key was pasted twice — using a single copy.")
    if not key.startswith("sk-ant-"):
        print(f"Warning: keys normally start with 'sk-ant-'; got '{key[:7]}…'.")

    print("Checking the key against the API…")
    try:
        import anthropic
        anthropic.Anthropic(api_key=key).messages.create(
            model=MODEL, max_tokens=8,
            messages=[{"role": "user", "content": "hi"}])
        print("Key works.")
    except ImportError:
        print("(anthropic SDK not installed — storing without checking.)")
    except Exception as e:
        print(f"Key rejected: {type(e).__name__}: {e}")
        print("Not stored. Re-copy the key from console.anthropic.com and retry.")
        return 1
    r = subprocess.run(
        ["security", "add-generic-password",
         "-a", os.environ.get("USER", ""), "-s", KEYCHAIN_SERVICE,
         "-w", key, "-U"],
        capture_output=True, text=True)
    if r.returncode != 0:
        print("Keychain write failed:", (r.stderr or "").strip())
        return 1
    print(f"Stored in the login Keychain as '{KEYCHAIN_SERVICE}'.")
    print("Run ./run.sh -- no export needed. macOS will ask once for permission;")
    print("choose 'Always Allow' so it stops asking.")
    return 0

# Chat is grounded in these every single turn -- that is what keeps advice stable
# across conversations instead of re-derived from whatever got pasted in. The
# sport-specific parts come from config so this prompt and the dashboard can't
# describe two different systems.
def build_preamble(cfg):
    c = cfg["coach"]
    safety = f"\nSafety: {c['safety'].strip()}\n" if c.get("safety", "").strip() else ""
    fields = ", ".join(f["key"] for f in cfg["journal"]["fields"]) or "none"
    # Kinds the athlete has declared untracked are named as such, so the coach
    # plans them freely without later reading their silence as a skipped day.
    kinds = ", ".join(
        f"{k} (untracked -- no data will ever exist for it either way)"
        if s.get("complete_when") == "untracked" else k
        for k, s in cfg["plan"]["kinds"].items()) or "work, rest"
    return f"""You are the athlete's {c['role']} for {c['system']}.

Below are three documents. Treat them as authoritative:

1. POLICY -- the standing rules. Binding, but internalised. You have read them
   and you now simply hold these principles the way an experienced coach does:
   give the reasoning, not the citation. Do not quote section numbers, do not
   say "per the policy" or "the policy says" or "§5" -- say what you think and
   why, in your own voice, as someone who happens to be right about it. If the
   athlete asks for something the rules forbid, tell them plainly it isn't the
   right call and what to do instead. Never quietly improvise around a rule.

   A question is not a challenge. "Why?", "are you sure?", "does that actually
   matter?" are requests for a short reason, and they get two or three
   sentences of plain mechanism in your own words -- the way an expert answers
   in a doorway, not the way one answers under oath. Do not quote anything, do
   not grade your confidence, do not list caveats, do not reach for headings or
   bullets. Just say the thing that is true and why it matters.

   Only open the book when they actually push: when they disagree with you,
   challenge a rule after you have already given them the short reason, or ask
   outright what the rules say. Then hold nothing back -- name the rule, quote
   it, say what it is protecting against, how much confidence it actually
   deserves, and where you think it is too conservative, too loose, or simply
   wrong for this athlete. Argue your side honestly rather than defending it
   because it is written down. A rule that survives being examined is worth
   more than one that was never questioned. If it doesn't survive, say it
   should be changed in policy.md with a version bump rather than ignored for
   one conversation.
2. CONTEXT -- generated from the athlete's device exports. Current loads,
   readiness, flags, recent sessions.
3. JOURNAL -- the athlete's own qualitative notes: how the body felt, sleep,
   life, events.

Style: direct and concrete. Give numbers. Short answers for short questions --
don't write an essay when a sentence does. You are talking to the person whose
body this is; they know their own history.

Default to a few sentences. Headings, bullet lists, graded confidence and
enumerated caveats are for when the athlete has asked for depth or is arguing
with you -- reaching for that structure unprompted is the single most common
way to get this wrong. A passing question deserves a passing answer.
{safety}
WRITING TO THE JOURNAL
The journal is the durable memory of this system -- it is read back into every
future conversation, while chat transcripts are only archived. So when the
athlete tells you something durable that isn't already recorded, capture it by
ending your reply with a line in exactly this form:

    [[journal: {config.journal_grammar(cfg)}]]

It is stripped from what they see and appended to journal.md verbatim.

WRITING A PLAN
You cannot write files. The only things that leave this conversation are the
two markers on this page — so if you describe a plan without the marker below,
nothing is saved and the dashboard goes on showing the old one. Never tell the
athlete you have updated, saved or written a plan unless you emitted it.

To publish a training block, wrap the whole plan document in:

    [[plan: YYYY-MM-DD]]
    # <title>
    …the full plan in markdown…
    [[/plan]]

The date is the plan's first day and becomes its filename; the newest file in
plans/ is the one the dashboard reads. The markers are stripped and the plan
itself stays visible, so write it for the athlete to read, not as machinery.

It MUST contain a fenced ```cycle block, one line per day, or the dashboard
renders no day strip:

    ```cycle
    YYYY-MM-DD | <kind> | <short title> | <detail>
    ```

Kinds available: {kinds}. Write every day of the block, including rest days.
Only emit a plan when asked for one, or when a rule clearly forces a change to
the current block — and say what changed and why.

Write one only for things worth re-reading weeks from now: {c['journal_examples']}.
The scored fields ({fields}) are optional -- omit any the athlete didn't mention,
and use the date they are describing, not necessarily today.

Do NOT write one for: questions, plan explanations, anything already in the
journal, or your own advice. Most turns need no journal line at all. One line
maximum per reply.
"""


def read(path, default=""):
    p = os.path.join(ROOT, path)
    if not os.path.exists(p):
        return default
    with open(p, encoding="utf-8") as f:
        return f.read()


def latest_plan_path():
    d = os.path.join(ROOT, "plans")
    if not os.path.isdir(d):
        return None
    ps = sorted(p for p in os.listdir(d) if p.endswith(".md"))
    return f"plans/{ps[-1]}" if ps else None


def parse_plan_days(md, by_date):
    """Pull the ```cycle block out of a plan file.

    Line format:  YYYY-MM-DD | kind | title | detail
    Any number of days, any start weekday -- the block is read as the list of
    dates it actually contains, not as a week. `done` is inferred from what
    actually got logged that day, so the strip ticks itself off as new data
    lands -- nothing to check by hand. `done` is None where that inference has
    nothing to work with because the day was never going to be exported.
    """
    out, inside = [], False
    for line in md.splitlines():
        s = line.strip()
        # ```week is the original fence name; plans written against it still
        # render, so nothing in plans/ needed rewriting when this generalised.
        if s.startswith("```cycle") or s.startswith("```week"):
            inside = True
            continue
        if inside and s.startswith("```"):
            break
        if not inside or "|" not in s:
            continue
        parts = [p.strip() for p in s.split("|")]
        if len(parts) < 3 or not parts[0][:4].isdigit():
            continue
        date, kind, title = parts[0], parts[1].lower(), parts[2]
        detail = parts[3] if len(parts) > 3 else ""
        d = by_date.get(date, {})
        prim = d.get("primary_km") or 0.0
        trimp = d.get("trimp") or 0.0
        acts = (d.get("activities") or "").lower()

        spec = CFG["plan"]["kinds"].get(kind, {})
        rule = spec.get("complete_when", "any_activity")
        if rule == "primary_volume":
            done = prim > 0
        elif rule == "activity_matches":
            done = any(w.lower() in acts for w in spec.get("match", []))
        elif rule == "always":
            done = True          # rest days need nothing
        elif rule == "untracked":
            # Work the athlete does but never records -- a floor set, a bike
            # commute. No export will ever mention it either way, so `done` is
            # not False and not True: it is unknown, and saying so is what
            # stops the day being read as a skipped one.
            done = None
        else:
            done = trimp > 0 or bool(acts)

        out.append({"date": date, "kind": kind, "title": title,
                    "detail": detail,
                    "done": None if done is None else bool(done),
                    "logged": d.get("activities") or "",
                    "primary_km": prim or None})
    return out


def rows(path):
    p = os.path.join(ROOT, path)
    if not os.path.exists(p):
        return []
    with open(p, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def fl(r, k):
    v = (r.get(k) or "").strip()
    if v in ("", "--"):
        return None
    try:
        return float(v)
    except ValueError:
        return None


def cycle_bars(daily, days, keep=26):
    """Total each load channel over fixed-width blocks, newest block first.

    Calendar weeks were the obvious bucketing and the wrong one. They impose a
    Monday start on anyone whose block isn't seven days long, and the current
    bucket is a partial week that reads as a crash in load. Counting backwards
    from the newest day of data instead means the last bar is always a full
    block ending today, and every bar is directly comparable to the chronic
    reference line the chart draws over it.
    """
    dated = sorted((r for r in daily if r.get("date")), key=lambda r: r["date"])
    if not dated:
        return []
    end = dt.date.fromisoformat(dated[-1]["date"])
    blocks = {}
    for r in dated:
        # 0 is the block ending on the newest day, 1 the one before it.
        i = (end - dt.date.fromisoformat(r["date"])).days // days
        e = blocks.get(i)
        if e is None:
            lo = end - dt.timedelta(days=days * i + days - 1)
            hi = end - dt.timedelta(days=days * i)
            e = blocks[i] = {"start": lo.isoformat(), "end": hi.isoformat(),
                             "label": f"{lo.isoformat()} → {hi.isoformat()}",
                             "primary_km": 0.0, "mech_km": 0.0, "trimp": 0.0}
        e["primary_km"] += fl(r, "primary_km") or 0.0
        # Weighted km -- the same unit the chronic average and ACWR are in, so
        # the chart can compare a bar to its reference line directly.
        e["mech_km"] += fl(r, "mech_km") or 0.0
        e["trimp"] += fl(r, "trimp") or 0.0
    out = [blocks[i] for i in sorted(blocks, reverse=True)][-keep:]
    for b in out:
        b["primary_km"] = round(b["primary_km"], 1)
        b["mech_km"] = round(b["mech_km"], 1)
        b["trimp"] = round(b["trimp"], 0)
    return out


def build_payload():
    daily = rows("data/daily.csv")
    sess = rows("data/sessions.csv")
    fm = CFG["form_metric"]
    cyc = CFG["cycle"]["days"]

    # Per-cycle aggregates -- the load channels, kept as separate charts
    bars = cycle_bars(daily, cyc)

    match = CFG["primary"]["match"]
    prim = [r for r in sess
            if match and any(m.lower() in (r.get("type") or "").lower() for m in match)]
    prim.sort(key=lambda r: r.get("datetime", ""))
    primary_sessions = [
        {
            "date": r.get("date"),
            "km": fl(r, "distance_km"),
            "form": fl(r, fm["field"]) if fm["enabled"] else None,
            "hr": fl(r, "avg_hr"),
            "pace": fl(r, "pace_s_per_km"),
            "te": fl(r, "aerobic_te"),
        }
        for r in prim
        if fl(r, "distance_km")
    ]

    # Headline stats
    dated = [r for r in daily if r.get("date")]
    dated.sort(key=lambda r: r["date"])
    last14 = dated[-14:]
    slept = [r for r in dated if fl(r, "sleep_score")]
    last_sleep = slept[-1] if slept else {}
    hrv_rows = [r for r in dated if fl(r, "hrv_night")]
    last_hrv = hrv_rows[-1] if hrv_rows else {}

    def wsum(rs, k):
        return round(sum(fl(r, k) or 0.0 for r in rs), 1)

    # ACWR keeps its standard 7:28-day definition whatever the cycle length is
    # -- the ceilings you'd read anywhere else are quoted against those windows.
    a7, c28 = dated[-7:], dated[-28:]
    ac_im, ch_im = wsum(a7, "mech_km"), round(wsum(c28, "mech_km") / 4, 2)
    ac_tr, ch_tr = wsum(a7, "trimp"), round(wsum(c28, "trimp") / 4, 1)
    # The chart's dashed line is that same chronic rate stretched to the width
    # of one bar, so bar-against-line stays readable at any cycle length.
    ref_im = round(wsum(c28, "mech_km") / 28 * cyc, 2)
    ref_tr = round(wsum(c28, "trimp") / 28 * cyc, 1)

    journal = [l for l in read("journal.md").splitlines() if l.startswith("20")]

    pp = latest_plan_path()
    plan_md = read(pp) if pp else ""
    by_date = {r["date"]: {"primary_km": fl(r, "primary_km"),
                           "trimp": fl(r, "trimp"),
                           "activities": r.get("activities")}
               for r in dated}

    return {
        "generated": dt.datetime.now().isoformat(timespec="seconds"),
        "today": dt.date.today().isoformat(),
        "config": CFG,
        "plan_name": os.path.basename(pp) if pp else None,
        "plan_days": parse_plan_days(plan_md, by_date),
        "bars": bars,
        "primary_sessions": primary_sessions,
        "last14": [
            {
                "date": r["date"], "dow": r.get("dow"),
                "primary_km": fl(r, "primary_km"), "trimp": fl(r, "trimp"),
                "sleep_score": fl(r, "sleep_score"), "rhr": fl(r, "rhr"),
                "hrv_night": fl(r, "hrv_night"), "bb": fl(r, "body_battery"),
                "activities": r.get("activities") or "",
            } for r in last14
        ],
        "stats": {
            "mech_acute": ac_im, "mech_chronic": ch_im, "mech_ref": ref_im,
            "mech_acwr": round(ac_im / ch_im, 2) if ch_im else None,
            "aerobic_acute": ac_tr, "aerobic_chronic": ch_tr,
            "aerobic_ref": ref_tr,
            "aerobic_acwr": round(ac_tr / ch_tr, 2) if ch_tr else None,
            "hrv_night": fl(last_hrv, "hrv_night"),
            "hrv_lo": fl(last_hrv, "hrv_base_lo"),
            "hrv_hi": fl(last_hrv, "hrv_base_hi"),
            "rhr": fl(last_sleep, "rhr"),
            "sleep_score": fl(last_sleep, "sleep_score"),
            "sleep_date": last_sleep.get("date"),
            "primary_km_cycle": wsum(dated[-cyc:], "primary_km"),
        },
        "journal": journal[-40:],
        "context_md": read("context.md"),
        "has_key": bool(resolve_api_key()[0]),
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body)
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.split("?")[0] in ("/", "/index.html"):
            return self._send(200, read("index.html"), "text/html; charset=utf-8")
        if self.path.startswith("/api/data"):
            return self._send(200, build_payload())
        self._send(404, {"error": "not found"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            return self._send(400, {"error": "bad json"})
        p = self.path.split("?")[0]
        if p == "/api/journal":
            return self.journal(body)
        if p == "/api/build":
            return self.build()
        if p == "/api/chat":
            return self.chat(body)
        self._send(404, {"error": "not found"})

    def journal(self, body):
        line = (body.get("line") or "").strip()
        if not line:
            return self._send(400, {"error": "empty"})
        with open(os.path.join(ROOT, "journal.md"), "a", encoding="utf-8") as f:
            f.write("\n" + line)
        return self._send(200, {"ok": True, "line": line})

    def build(self):
        global CFG
        r = subprocess.run([sys.executable, "build.py"], cwd=ROOT,
                           capture_output=True, text=True, timeout=120)
        # Picked up without a restart, so editing config.json and hitting
        # Rebuild is the whole loop.
        CFG = config.load(force=True)
        return self._send(200, {"ok": r.returncode == 0,
                                "out": (r.stdout + r.stderr).strip()})

    JOURNAL_RE = re.compile(r"\[\[journal:\s*(.+?)\s*\]\]", re.S)
    # A plan is multi-line and contains its own ``` fences, so it needs a
    # closing marker rather than a single bracketed line.
    PLAN_RE = re.compile(
        r"\[\[plan:\s*(\d{4}-\d{2}-\d{2})\s*\]\]\s*(.*?)\s*\[\[/plan\]\]", re.S)

    def persist_turn(self, question, reply):
        """Archive every exchange; promote the markers to real files.

        Three different jobs. The transcript is a full record you can search
        later. The journal is the distilled memory read back into every future
        conversation. A plan is the artefact the dashboard renders its day strip
        from. Chat that changes nothing durable belongs only in the first.

        -> (journal_line | None, plan_info | None)
        """
        os.makedirs(os.path.join(ROOT, "chats"), exist_ok=True)
        now = dt.datetime.now()
        path = os.path.join(ROOT, "chats", f"{now:%Y-%m}.md")
        # The journal marker is bookkeeping and comes out. A plan is the answer
        # to what was asked, so only its markers go -- the body stays readable
        # in the transcript.
        clean = self.JOURNAL_RE.sub("", reply)
        clean = self.PLAN_RE.sub(lambda m: m.group(2), clean).strip()
        with open(path, "a", encoding="utf-8") as f:
            if f.tell() == 0:
                f.write(f"# Chat transcript — {now:%B %Y}\n")
            f.write(f"\n## {now:%Y-%m-%d %H:%M}\n\n"
                    f"**me:** {question.strip()}\n\n**coach:** {clean}\n")

        return self.save_journal(reply), self.save_plan(reply)

    def save_journal(self, reply):
        m = self.JOURNAL_RE.search(reply)
        if not m:
            return None
        line = " ".join(m.group(1).split())
        if not line[:4].isdigit():          # must start with a date
            return None
        jp = os.path.join(ROOT, "journal.md")
        if line in read("journal.md"):       # don't duplicate
            return None
        with open(jp, "a", encoding="utf-8") as f:
            f.write("\n" + line)
        return line

    def save_plan(self, reply):
        """Write a [[plan: YYYY-MM-DD]] … [[/plan]] block to plans/.

        Without this the coach can compose a plan and has nowhere to put it,
        which it has no way to discover -- so it reports success and the
        dashboard goes on rendering last week. The day strip reads the
        newest file in plans/, so writing one is what actually changes the plan.
        """
        m = self.PLAN_RE.search(reply)
        if not m:
            return None
        day, bodymd = m.group(1), m.group(2).strip()
        if not bodymd:
            return None
        os.makedirs(os.path.join(ROOT, "plans"), exist_ok=True)
        rel = f"plans/{day}.md"
        full = os.path.join(ROOT, rel)
        existed = os.path.exists(full)
        with open(full, "w", encoding="utf-8") as f:
            f.write(bodymd.rstrip() + "\n")
        # A plan with no cycle fence renders no day strip. Say so rather than
        # letting it look saved-but-broken.
        has_fence = bool(re.search(r"```(cycle|week)\b", bodymd))
        return {"file": rel, "replaced": existed, "has_fence": has_fence}

    def chat(self, body):
        if not resolve_api_key()[0]:
            return self._send(200, {"error":
                "No API key found. Get one at console.anthropic.com, then store "
                "it once:\n\n  ./.venv/bin/python serve.py --set-key\n\n"
                "and restart with ./run.sh — no export needed after that."})
        try:
            import anthropic
        except ImportError:
            return self._send(200, {"error":
                "anthropic SDK missing. Run: ./.venv/bin/pip install anthropic"})

        msgs = body.get("messages") or []
        if not msgs:
            return self._send(400, {"error": "no messages"})

        journal = "\n".join(
            l for l in read("journal.md").splitlines() if l.startswith("20"))
        plans = sorted(os.listdir(os.path.join(ROOT, "plans"))) \
            if os.path.isdir(os.path.join(ROOT, "plans")) else []
        latest_plan = read(f"plans/{plans[-1]}") if plans else "(none yet)"

        system = [{
            "type": "text",
            "text": (f"{build_preamble(CFG)}\n\n"
                     f"===== POLICY (binding) =====\n{read('policy.md')}\n\n"
                     f"===== CONTEXT (generated) =====\n{read('context.md')}\n\n"
                     f"===== LATEST PLAN =====\n{latest_plan}\n\n"
                     f"===== JOURNAL =====\n{journal}\n"),
            # Stable prefix across turns -- cache it.
            "cache_control": {"type": "ephemeral"},
        }]

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        def emit(obj):
            self.wfile.write(f"data: {json.dumps(obj)}\n\n".encode("utf-8"))
            self.wfile.flush()

        try:
            client = anthropic.Anthropic()
            with client.messages.stream(
                model=MODEL,
                max_tokens=16000,
                thinking={"type": "adaptive"},
                output_config={"effort": "high"},
                system=system,
                messages=[{"role": m["role"], "content": m["content"]}
                          for m in msgs],
            ) as stream:
                for ev in stream:
                    if ev.type == "content_block_delta":
                        if ev.delta.type == "text_delta":
                            emit({"t": ev.delta.text})
                final = stream.get_final_message()
                if final.stop_reason == "refusal":
                    emit({"t": "\n\n_(response stopped: refusal)_"})

                reply = "".join(b.text for b in final.content if b.type == "text")
                logged, planned = self.persist_turn(msgs[-1]["content"], reply)
                emit({"done": True, "journal": logged, "plan": planned,
                      "usage": {"in": final.usage.input_tokens,
                                "out": final.usage.output_tokens}})
        except BrokenPipeError:
            pass
        except Exception as e:  # surface the real error in the UI
            try:
                emit({"error": f"{type(e).__name__}: {e}"})
            except Exception:
                pass


if __name__ == "__main__":
    if "--set-key" in sys.argv:
        raise SystemExit(store_key_in_keychain())

    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    key, src = resolve_api_key()
    status = f"found via {src}" if key else \
        "MISSING — chat disabled. Run: ./.venv/bin/python serve.py --set-key"
    print(f"  dashboard  http://127.0.0.1:{PORT}")
    print(f"  model      {MODEL}")
    print(f"  api key    {status}")
    print("  ctrl-c to stop")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
