#!/usr/bin/env python3
"""What ingest tells you it did.

Every parse failure in an adapter is a skip, and a silent skip shows up much
later as an empty chart. The Report is how a skip gets a name: which file, how
many rows, and which config key or column would have kept them.

Adapters get one of these handed to them and should use it liberally — a line
of output beats a debugging session.
"""

from __future__ import annotations


class Report:
    def __init__(self):
        self.files = []   # one row per file read
        self.warns = []   # things that cost you data, or might
        self.notes = []   # things worth knowing that cost you nothing

    # -- adapters call these --
    def file(self, name, kind, read, kept, decimal="", note=""):
        """One line per input file: how many rows went in, how many survived."""
        self.files.append({"file": name, "kind": kind, "read": read,
                           "kept": kept, "decimal": decimal, "note": note})

    def warn(self, line):
        self.warns.append(line)

    def note(self, line):
        self.notes.append(line)

    def missing_columns(self, kind, csvfile, columns, config_key=None):
        """Report column names that aren't in the header. `columns` is the
        adapter's map of role -> column name; a name that isn't there would
        otherwise ingest as a silent column of nulls."""
        absent = csvfile.missing_columns(columns.values()
                                         if isinstance(columns, dict)
                                         else columns)
        if absent:
            where = f" — fix or null out {config_key}" if config_key else ""
            self.warn(f"{kind}: no column named "
                      f"{', '.join(repr(a) for a in absent)}{where}")
        return absent

    def skipped(self, kind, bad, what, hint):
        """`bad` is the list of raw values that failed to parse. Names the first
        one, because the fix is nearly always the same for all of them."""
        if bad:
            self.warn(f"{kind}: {len(bad)} row{'s' if len(bad) > 1 else ''} "
                      f"skipped, unparsed {what} {bad[0]!r} — {hint}")

    # -- build.py calls this --
    def print(self):
        if self.files:
            w = max(len(f["file"]) for f in self.files)
            print("Ingest")
            for f in self.files:
                dec = f"  {f['decimal']}-decimal" if f["decimal"] else ""
                note = f"  ({f['note']})" if f["note"] else ""
                print(f"  {f['file']:<{w}}  {f['kind']:<10} "
                      f"read {f['read']:>5}  kept {f['kept']:>5}  "
                      f"skipped {f['read'] - f['kept']:>4}{dec}{note}")
        for line in self.notes:
            print(f"  · {line}")
        for line in self.warns:
            print(f"  ! {line}")

    @property
    def rows_kept(self):
        return sum(f["kept"] for f in self.files)
