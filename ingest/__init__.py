#!/usr/bin/env python3
"""Ingestion — the only part of this repo that knows what your source exports.

    data/raw/*  ->  [ adapter ]  ->  sessions / sleep / hrv  ->  [ the engine ]
                     swappable        the contract in            never changes
                                      schema.py

Everything upstream of the arrow is vendor-shaped and belongs in an adapter.
Everything downstream — TRIMP, ACWR, the accumulating CSVs, context.md, the
dashboard, the coach — reads only the records schema.py describes, and so needs
no edit when you switch from a Garmin CSV to a Strava export to a Health app
dump.

An adapter is one Python file with one function:

    CONTRACT = 1                      # schema version it was written against
    DESCRIPTION = "Strava bulk export"

    def ingest(raw_dir, cfg, report):
        return Ingested(sessions=[...], sleep={...}, hrv={...})

Drop it in `ingest/adapters/local/` (yours, upstream never touches that folder)
or `ingest/adapters/` (shipped with the repo), and select it with
`config.source.adapter`. A file in local/ shadows a shipped one of the same
name, so you can bend garmin_csv without editing a tracked file.

    python3 -m ingest           list adapters, show which one is active
    python3 -m ingest --check   run it over data/raw and report field coverage
"""

from __future__ import annotations

import importlib.util
import os
import sys

from . import schema
from .report import Report
from .schema import CONTRACT

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADAPTER_DIRS = [
    ("shipped", os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "adapters")),
    ("local", os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "adapters", "local")),
]

DEFAULT_ADAPTER = "garmin_csv"


class Ingested:
    """What an adapter returns. Empty collections are fine and normal — a
    source with no HRV export just leaves hrv alone."""

    def __init__(self, sessions=None, sleep=None, hrv=None):
        self.sessions = list(sessions or [])
        self.sleep = dict(sleep or {})
        self.hrv = dict(hrv or {})

    def __repr__(self):
        return (f"Ingested(sessions={len(self.sessions)}, "
                f"sleep={len(self.sleep)}, hrv={len(self.hrv)})")


class AdapterError(Exception):
    """Wrong adapter name, or an adapter that won't import."""


def available():
    """-> {name: {"origin", "path", "description", "contract"}}, local last so
    it wins. Reads the file's docstring rather than importing it, so a broken
    adapter still shows up in the list instead of taking the list down."""
    found = {}
    for origin, d in ADAPTER_DIRS:
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if not fn.endswith(".py") or fn.startswith("_"):
                continue
            name = fn[:-3]
            found[name] = {"origin": origin, "path": os.path.join(d, fn),
                           **_peek(os.path.join(d, fn))}
    return found


def _peek(path):
    """DESCRIPTION and CONTRACT without executing the module."""
    desc, contract = "", None
    try:
        with open(path, encoding="utf-8") as f:
            head = f.read(4000)
    except OSError:
        return {"description": "", "contract": None}
    for line in head.splitlines():
        s = line.strip()
        if s.startswith("DESCRIPTION") and "=" in s and not desc:
            desc = s.split("=", 1)[1].strip().strip('"\'')
        if s.startswith("CONTRACT") and "=" in s and contract is None:
            try:
                contract = int(s.split("=", 1)[1].split("#")[0].strip())
            except ValueError:
                pass
    return {"description": desc, "contract": contract}


def adapter_name(cfg):
    return (cfg.get("source", {}) or {}).get("adapter") or DEFAULT_ADAPTER


def load_adapter(name):
    """Import an adapter by name. -> module"""
    have = available()
    if name not in have:
        known = ", ".join(sorted(have)) or "none found"
        raise AdapterError(
            f"config.source.adapter = {name!r}, but there is no "
            f"ingest/adapters/{name}.py. Available: {known}")
    path = have[name]["path"]
    # Adapters import the toolkit absolutely (`from ingest import parsers`), so
    # they load the same way whether they sit in adapters/ or adapters/local/.
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    spec = importlib.util.spec_from_file_location(f"ingest._adapter_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception as e:  # a user-written file; say where, not just what
        raise AdapterError(f"{os.path.relpath(path, ROOT)} failed to import "
                           f"({type(e).__name__}: {e})") from e
    if not hasattr(mod, "ingest"):
        raise AdapterError(f"{os.path.relpath(path, ROOT)} has no ingest() "
                           f"function — see ingest/README.md for the contract")
    mod.NAME = name
    return mod


def run(raw_dir, cfg, report=None):
    """Ingest data/raw with the configured adapter, validated against the
    schema. -> Ingested. Raises AdapterError if the adapter can't be used;
    everything softer than that becomes a line in the report."""
    report = report or Report()
    mod = load_adapter(adapter_name(cfg))
    written_for = getattr(mod, "CONTRACT", None)
    if written_for is not None and written_for != CONTRACT:
        report.warn(f"adapter {mod.NAME!r} was written for ingest contract "
                    f"v{written_for}, this repo is v{CONTRACT} — check "
                    f"ingest/schema.py for what changed")
    got = mod.ingest(raw_dir, cfg, report)
    if not isinstance(got, Ingested):
        raise AdapterError(f"adapter {mod.NAME!r} returned "
                           f"{type(got).__name__}, expected ingest.Ingested")
    return schema.validate(got, report)


def hint(cfg):
    """One line telling the user what to put in data/raw for this adapter."""
    try:
        mod = load_adapter(adapter_name(cfg))
    except AdapterError as e:
        return str(e)
    fn = getattr(mod, "hint", None)
    return fn(cfg) if fn else f"Export from {adapter_name(cfg)} into data/raw/."
