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

ROOT = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("PORT", "8765"))
MODEL = "claude-opus-5"
KEYCHAIN_SERVICE = "anthropic-api-key"


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
# across conversations instead of re-derived from whatever got pasted in.
SYSTEM_PREAMBLE = """You are the athlete's running coach for a shin-aware training system.

Below are three documents. Treat them as authoritative:

1. POLICY -- the standing rules. These are binding. If the athlete asks for
   something the policy forbids, say so plainly, cite the section, and offer the
   compliant alternative. Do not quietly improvise around a rule. If a rule
   genuinely seems wrong, say that it should be changed in policy.md with a
   version bump rather than ignored for one conversation.
2. CONTEXT -- generated from Garmin exports. Current loads, readiness, flags,
   recent sessions.
3. JOURNAL -- the athlete's own qualitative notes: shins, sleep, life, events.

Style: direct and concrete. Give numbers. Short answers for short questions --
don't write an essay when a sentence does. You are talking to the person whose
body this is; they know their own history.

Safety: if they report focal point tenderness on the bone, pain at rest or at
night, pain worsening week over week, or limping, say clearly that this is the
escalation pattern in policy section 8 and belongs with a physio, not a plan
adjustment.

WRITING TO THE JOURNAL
The journal is the durable memory of this system -- it is read back into every
future conversation, while chat transcripts are only archived. So when the
athlete tells you something durable that isn't already recorded, capture it by
ending your reply with a line in exactly this form:

    [[journal: YYYY-MM-DD | shin L#/R# | note: ...]]

It is stripped from what they see and appended to journal.md verbatim.

Write one only for things worth re-reading weeks from now: a shin score, how a
run actually felt, next-morning symptoms, a life event affecting training,
equipment changes, a goal change. The shin field is optional -- omit it if they
didn't mention shins, and use the date they are describing, not necessarily
today.

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


def parse_week(md, by_date):
    """Pull the ```week block out of a plan file.

    Line format:  YYYY-MM-DD | kind | title | detail
    `done` is inferred from what actually got logged that day, so the strip
    ticks itself off as Garmin data lands -- nothing to check by hand.
    """
    out, inside = [], False
    for line in md.splitlines():
        s = line.strip()
        if s.startswith("```week"):
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
        run_km = d.get("run_km") or 0.0
        trimp = d.get("trimp") or 0.0
        acts = (d.get("activities") or "").lower()
        if kind == "run":
            done = run_km > 0
        elif kind == "cross":
            done = trimp > 0 or bool(acts)
        elif kind == "strength":
            done = any(w in acts for w in ("other", "floor", "strength"))
        else:
            done = True  # rest days need nothing
        out.append({"date": date, "kind": kind, "title": title,
                    "detail": detail, "done": bool(done),
                    "logged": d.get("activities") or "",
                    "run_km": run_km or None})
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


def iso_week(d):
    y, w, _ = dt.date.fromisoformat(d).isocalendar()
    return f"{y}-W{w:02d}"


def build_payload():
    daily = rows("data/daily.csv")
    sess = rows("data/sessions.csv")

    # Weekly aggregates -- the two load channels, kept as separate charts
    weeks = {}
    for r in daily:
        if not r.get("date"):
            continue
        k = iso_week(r["date"])
        e = weeks.setdefault(k, {"week": k, "run_km": 0.0, "impact_km": 0.0,
                                 "trimp": 0.0, "start": r["date"]})
        e["run_km"] += fl(r, "run_km") or 0.0
        # Weighted km -- the same unit the chronic average and ACWR are in, so
        # the impact chart can compare a bar to its reference line directly.
        e["impact_km"] += fl(r, "impact_km") or 0.0
        e["trimp"] += fl(r, "trimp") or 0.0
        e["start"] = min(e["start"], r["date"])
    weekly = sorted(weeks.values(), key=lambda x: x["start"])[-26:]
    for w in weekly:
        w["run_km"] = round(w["run_km"], 1)
        w["impact_km"] = round(w["impact_km"], 1)
        w["trimp"] = round(w["trimp"], 0)

    runs = [r for r in sess if "Running" in (r.get("type") or "")]
    runs.sort(key=lambda r: r.get("datetime", ""))
    run_pts = [
        {
            "date": r.get("date"),
            "km": fl(r, "distance_km"),
            "cadence": fl(r, "cadence"),
            "hr": fl(r, "avg_hr"),
            "pace": fl(r, "pace_s_per_km"),
            "te": fl(r, "aerobic_te"),
        }
        for r in runs
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

    a7 = dated[-7:]
    c28 = dated[-28:]
    ac_im, ch_im = wsum(a7, "impact_km"), round(wsum(c28, "impact_km") / 4, 2)
    ac_tr, ch_tr = wsum(a7, "trimp"), round(wsum(c28, "trimp") / 4, 1)

    journal = [l for l in read("journal.md").splitlines() if l.startswith("20")]

    pp = latest_plan_path()
    plan_md = read(pp) if pp else ""
    by_date = {r["date"]: {"run_km": fl(r, "run_km"), "trimp": fl(r, "trimp"),
                           "activities": r.get("activities")}
               for r in dated}

    return {
        "generated": dt.datetime.now().isoformat(timespec="seconds"),
        "today": dt.date.today().isoformat(),
        "plan_name": os.path.basename(pp) if pp else None,
        "week": parse_week(plan_md, by_date),
        "weekly": weekly,
        "runs": run_pts,
        "last14": [
            {
                "date": r["date"], "dow": r.get("dow"),
                "run_km": fl(r, "run_km"), "trimp": fl(r, "trimp"),
                "sleep_score": fl(r, "sleep_score"), "rhr": fl(r, "rhr"),
                "hrv_night": fl(r, "hrv_night"), "bb": fl(r, "body_battery"),
                "activities": r.get("activities") or "",
            } for r in last14
        ],
        "stats": {
            "impact_acute": ac_im, "impact_chronic": ch_im,
            "impact_acwr": round(ac_im / ch_im, 2) if ch_im else None,
            "aerobic_acute": ac_tr, "aerobic_chronic": ch_tr,
            "aerobic_acwr": round(ac_tr / ch_tr, 2) if ch_tr else None,
            "hrv_night": fl(last_hrv, "hrv_night"),
            "hrv_lo": fl(last_hrv, "hrv_base_lo"),
            "hrv_hi": fl(last_hrv, "hrv_base_hi"),
            "rhr": fl(last_sleep, "rhr"),
            "sleep_score": fl(last_sleep, "sleep_score"),
            "sleep_date": last_sleep.get("date"),
            "run_km_7d": wsum(a7, "run_km"),
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
        r = subprocess.run([sys.executable, "build.py"], cwd=ROOT,
                           capture_output=True, text=True, timeout=120)
        return self._send(200, {"ok": r.returncode == 0,
                                "out": (r.stdout + r.stderr).strip()})

    JOURNAL_RE = re.compile(r"\[\[journal:\s*(.+?)\s*\]\]", re.S)

    def persist_turn(self, question, reply):
        """Archive every exchange; promote any [[journal: …]] line to journal.md.

        Two different jobs: the transcript is a full record you can search later,
        the journal is the distilled memory that gets read back into every future
        conversation. Chat that changes nothing durable belongs only in the first.
        """
        os.makedirs(os.path.join(ROOT, "chats"), exist_ok=True)
        now = dt.datetime.now()
        path = os.path.join(ROOT, "chats", f"{now:%Y-%m}.md")
        clean = self.JOURNAL_RE.sub("", reply).strip()
        with open(path, "a", encoding="utf-8") as f:
            if f.tell() == 0:
                f.write(f"# Chat transcript — {now:%B %Y}\n")
            f.write(f"\n## {now:%Y-%m-%d %H:%M}\n\n"
                    f"**me:** {question.strip()}\n\n**coach:** {clean}\n")

        m = self.JOURNAL_RE.search(reply)
        if not m:
            return None
        line = " ".join(m.group(1).split())
        if not line[:4].isdigit():          # must start with a date
            return None
        jp = os.path.join(ROOT, "journal.md")
        existing = read("journal.md")
        if line in existing:                 # don't duplicate
            return None
        with open(jp, "a", encoding="utf-8") as f:
            f.write("\n" + line)
        return line

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
            "text": (f"{SYSTEM_PREAMBLE}\n\n"
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
                logged = self.persist_turn(msgs[-1]["content"], reply)
                emit({"done": True, "journal": logged,
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
