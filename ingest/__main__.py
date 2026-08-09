#!/usr/bin/env python3
"""Inspect and test ingestion without running the rest of the pipeline.

    python3 -m ingest             which adapters exist, which one is active
    python3 -m ingest --check     run the active one over data/raw/ and report
    python3 -m ingest --check -a strava     ...using a specific adapter
    python3 -m ingest --readers   single-session readers and the extensions
                                  they claim (the data/activities/ path)

--check writes nothing. It is the loop to work in while getting an adapter
right: it prints the ingest report, then how many records carried each field
and a sample record, which is how you catch a column that mapped to nothing or
a duration that came out 60x too big.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import ingest  # noqa: E402
from ingest import schema  # noqa: E402

RAW = os.path.join(ingest.ROOT, "data", "raw")


def list_adapters(cfg):
    active = ingest.adapter_name(cfg)
    have = ingest.available()
    print(f"ingest contract v{schema.CONTRACT}   raw dir: "
          f"{os.path.relpath(RAW, ingest.ROOT)}/\n")
    if not have:
        print("No adapters found in ingest/adapters/.")
        return
    w = max(len(n) for n in have)
    for name, meta in sorted(have.items()):
        mark = "*" if name == active else " "
        ver = meta["contract"]
        flag = "" if ver in (None, schema.CONTRACT) else f"  [written for v{ver}]"
        print(f" {mark} {name:<{w}}  {meta['origin']:<7}  "
              f"{meta['description'] or '—'}{flag}")
    print(f"\n* = active (config.source.adapter). Set it in config.json:\n"
          f'    "source": {{ "adapter": "{active}" }}')


def list_readers():
    """The other ingestion path: one file, one session, sampled per second.
    Readers are picked by extension rather than named in config, so there is
    no active one to mark — every listed extension simply works."""
    from ingest import activity

    have = activity.available()
    print(f"activity contract v{activity.CONTRACT}   drop dir: "
          f"data/activities/\n")
    if not have:
        print("No readers found in ingest/readers/.")
        return
    w = max(len(n) for n in have)
    for name, meta in sorted(have.items()):
        ver = meta["contract"]
        flag = "" if ver in (None, activity.CONTRACT) else f"  [written for v{ver}]"
        exts = " ".join(meta["extensions"]) or "— claims no extension"
        print(f"   {name:<{w}}  {meta['origin']:<7}  {exts:<10}  "
              f"{meta['description'] or '—'}{flag}")
    print("\nChosen by file extension — nothing to set in config.json.\n"
          "Write another: cp ingest/readers/_template.py "
          "ingest/readers/local/<fmt>.py")


def coverage(records, fields, label):
    """How many records carried each field. A column that mapped to nothing
    shows up here as 0 long before it shows up as an empty chart."""
    n = len(records)
    print(f"\n{label}: {n} record{'s' if n != 1 else ''}")
    if not n:
        return
    rows = records if isinstance(records, list) else list(records.values())
    seen = sorted({k for r in rows for k in r},
                  key=lambda k: (list(fields).index(k) if k in fields else 99))
    for k in seen:
        got = sum(1 for r in rows if r.get(k) not in (None, ""))
        bar = "#" * round(20 * got / n)
        extra = "" if k in fields else "  (extra)"
        flag = "   <-- empty" if got == 0 else ""
        print(f"    {k:<16} {got:>5}/{n:<5} {bar:<20}{extra}{flag}")


def sample(records, label):
    rows = records if isinstance(records, list) else list(records.values())
    if not rows:
        return
    r = max(rows, key=lambda x: sum(v not in (None, "") for v in x.values()))
    print(f"\n  most-complete {label}:")
    for k, v in r.items():
        if v not in (None, ""):
            print(f"    {k:<16} {v!r}")


def check(cfg, name=None):
    if name:
        cfg.setdefault("source", {})["adapter"] = name
    print(f"adapter: {ingest.adapter_name(cfg)}\n")
    if not os.path.isdir(RAW):
        print(f"No {os.path.relpath(RAW, ingest.ROOT)}/ yet.\n{ingest.hint(cfg)}")
        return 1
    rep = ingest.Report()
    try:
        got = ingest.run(RAW, cfg, rep)
    except ingest.AdapterError as e:
        print(f"! {e}")
        return 1
    rep.print()
    coverage(got.sessions, schema.SESSION_FIELDS, "sessions")
    coverage(got.sleep, schema.SLEEP_FIELDS, "sleep")
    coverage(got.hrv, schema.HRV_FIELDS, "hrv")
    sample(got.sessions, "session")
    sample(got.sleep, "sleep night")

    print()
    if not got.sessions:
        print("! no sessions — nothing downstream will have any load in it")
    elif not any(s.get("avg_hr") for s in got.sessions):
        print("! no session has avg_hr — TRIMP, and so every load figure, "
              "will be zero")
    else:
        print("Looks ingestible. `python3 build.py` to write it through.")
    return 0


def main(argv):
    cfg = config.load()
    name = None
    if "-a" in argv:
        name = argv[argv.index("-a") + 1]
    elif "--adapter" in argv:
        name = argv[argv.index("--adapter") + 1]
    if "--readers" in argv:
        list_readers()
        return 0
    if "--check" in argv:
        return check(cfg, name)
    list_adapters(cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
