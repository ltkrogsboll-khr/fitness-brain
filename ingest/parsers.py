#!/usr/bin/env python3
"""Parsing toolkit for ingest adapters.

Everything here is vendor-neutral: finding files, reading a CSV, and getting a
number, a date or a duration out of a cell that was written by a device vendor
who had no idea you would ever read it.

Adapters are not required to use any of it — an adapter that reads JSON or
SQLite just returns the same records some other way. But most exports are CSV,
and the fiddly parts (decimal commas, five duration notations, timezone-tagged
timestamps) are already solved here.

The one piece of state worth understanding is the decimal convention. '6,56' is
six-point-five-six in half of Europe and a syntax error in the other half, and
which one it is depends on the *file*, not on the field. So it lives on CsvFile,
and every number you pull out of that file is parsed in its convention.
"""

from __future__ import annotations

import csv
import os
import re
from datetime import date, datetime, timedelta

# Cell values that mean "no reading". Overridable per file via config.
MISSING = ("", "--", "---", "N/A", "n/a", "null")


# --- Finding files -----------------------------------------------------------
def route_files(raw_dir, patterns, report=None, ext=".csv"):
    """Sort the files in raw_dir into kinds by filename substring.

    `patterns` is {kind: substring}, e.g. {"activities": "activities"}. Returns
    {kind: [paths]}, sorted by name. Files matching nothing, and files matching
    two kinds, are reported rather than silently dropped — "the dashboard is
    empty" is a much worse bug report than "Activities-2.csv matched nothing".
    """
    out = {k: [] for k in patterns}
    for fn in sorted(os.listdir(raw_dir)):
        if ext and not fn.lower().endswith(ext):
            continue
        low = fn.lower()
        hits = [k for k, v in patterns.items() if v and v.lower() in low]
        if not hits:
            if report:
                report.warn(f"{fn}: matches no pattern in config.source.files "
                            f"({', '.join(repr(v) for v in patterns.values())})"
                            f" — not read")
            continue
        if len(hits) > 1 and report:
            report.warn(f"{fn}: matches {', '.join(hits)} — read as {hits[0]}; "
                        f"make config.source.files patterns more specific")
        out[hits[0]].append(os.path.join(raw_dir, fn))
    return out


# --- Decimal convention ------------------------------------------------------
# '6,56' is unambiguous evidence for a decimal comma, '6.56' for a decimal dot.
# Thousands separators ('1,024', '7.032') match neither on purpose -- three
# trailing digits is exactly the ambiguous case, so it gets no vote.
_COMMA_DEC = re.compile(r"\d,\d{1,2}(?!\d)")
_DOT_DEC = re.compile(r"\d\.\d{1,2}(?!\d)")
_THOUSAND_DOT = re.compile(r"-?\d{1,3}(\.\d{3})+")


def sniff_decimal(rows):
    """Vote on a decimal convention from the values themselves. -> comma | dot"""
    comma = dot = 0
    for r in rows:
        for v in r.values():
            if isinstance(v, str) and ("," in v or "." in v):
                comma += len(_COMMA_DEC.findall(v))
                dot += len(_DOT_DEC.findall(v))
    return "comma" if comma > dot else "dot"


def num(v, decimal="dot", missing=MISSING):
    """Parse a numeric cell. -> float | None, never raises."""
    if v is None:
        return None
    s = str(v).strip().strip('"').lstrip("'")
    if s in missing:
        return None
    if decimal == "comma":
        if "," in s:  # '1.234,5' -- a dot alongside it can only be thousands
            s = s.replace(".", "").replace(",", ".")
        elif _THOUSAND_DOT.fullmatch(s):
            s = s.replace(".", "")  # '7.032' steps -> 7032
    else:
        s = s.replace(",", "")  # in a dot locale a comma can only be thousands
    try:
        return float(s)
    except ValueError:
        return None


# --- Durations ---------------------------------------------------------------
# '6h 24min', '45 min', '1h 5min 30s'. Needs at least one unit letter, so a bare
# number falls through to the 'minutes'/'seconds' formats instead of matching here.
_HM_TEXT = re.compile(r"(?:(\d+)\s*h)?\s*(?:(\d+)\s*m(?:in)?)?\s*(?:(\d+)\s*s)?",
                      re.IGNORECASE)

DURATION_FORMATS = ("hms", "hm", "hm_text", "minutes", "seconds")


def seconds(v, formats=("hms",), decimal="dot", missing=MISSING):
    """Seconds from a duration or pace cell, trying `formats` in order.

      hms      '00:44:47', '6:50'  -- right-aligned, last part is seconds
      hm       '6:24'              -- left-aligned, first part is hours
      hm_text  '6h 24min'          -- needs a unit letter
      minutes  '384'               -- bare number of minutes
      seconds  '23087'             -- bare number of seconds

    Order matters: ':' shapes are tried before bare numbers, so listing both
    'hms' and 'minutes' handles an export that mixes them.
    """
    if v is None:
        return None
    s = str(v).strip().strip('"')
    if s in missing or not s:
        return None
    for f in formats:
        if f in ("hms", "hm") and ":" in s:
            try:
                parts = [float(p.replace(",", ".")) for p in s.split(":")]
            except ValueError:
                continue
            while len(parts) < 3:
                # '6:50' is mm:ss under hms, but hh:mm under hm
                if f == "hms":
                    parts.insert(0, 0.0)
                else:
                    parts.append(0.0)
            return parts[0] * 3600 + parts[1] * 60 + parts[2]
        if f == "hm_text":
            m = _HM_TEXT.fullmatch(s)
            if m and any(m.groups()):
                h, mi, sec = (int(g or 0) for g in m.groups())
                return h * 3600 + mi * 60 + sec
        if f in ("minutes", "seconds"):
            n = num(s, decimal, missing)
            if n is not None:
                return n * 60 if f == "minutes" else n
    return None


# --- Dates -------------------------------------------------------------------
def parse_dt(s, formats):
    """First strptime format that fits, or None. Timezones are converted to
    local and dropped -- a training log is read in the timezone it was lived in."""
    if s is None:
        return None
    s = str(s).strip().strip('"')
    for f in formats:
        try:
            d = datetime.strptime(s, f)
        except ValueError:
            continue
        return d.astimezone().replace(tzinfo=None) if d.tzinfo else d
    return None


def parse_date(s, formats):
    d = parse_dt(s, formats)
    return d.date() if d else None


MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], start=1)}


def infer_year_less_date(raw, cursor, today=None):
    """Some exports date rows '8 Aug', with no year.

    Rows are consecutive descending days, so anchor the newest to the most
    recent matching date <= today and walk back from there. Pass the cursor it
    returns back in on the next row. -> (date|None, cursor).

    English month abbreviations only. If your export has a real year in it, use
    parse_date instead — this is the fallback, not the path.
    """
    today = today or date.today()
    m = re.match(r"(\d{1,2})\s+([A-Za-z]{3})", str(raw))
    if not m:
        return None, cursor
    day, mon = int(m.group(1)), MONTHS.get(m.group(2)[:3].title())
    if not mon:
        return None, cursor
    if cursor is None:
        try:
            cand = date(today.year, mon, day)
        except ValueError:
            return None, cursor
        if cand > today:
            cand = date(today.year - 1, mon, day)
        cursor = cand
    else:
        cursor = cursor - timedelta(days=1)
        # self-heal if the file ever skips a day
        if cursor.day != day or cursor.month != mon:
            try:
                cursor = date(cursor.year, mon, day)
            except ValueError:
                pass
    return cursor, cursor


# --- Odd shapes --------------------------------------------------------------
def strip_unit(v, decimal="dot", missing=MISSING):
    """'57ms' -> 57.0 -- drops whatever unit suffix the export writes."""
    if v is None or str(v).strip() in missing:
        return None
    return num(re.sub(r"[^\d,.\-]", "", str(v)), decimal, missing)


def two_nums(v, decimal="dot", missing=MISSING):
    """'63ms - 78ms' -> (63.0, 78.0). Any two numbers in the cell will do."""
    if v is None or str(v).strip() in missing:
        return None, None
    got = re.findall(r"\d+(?:[.,]\d+)?", str(v))
    return ((num(got[0], decimal, missing), num(got[1], decimal, missing))
            if len(got) >= 2 else (None, None))


def first_percent(v, decimal="dot", missing=MISSING):
    """'49,5% L / 50,5% R' -> 49.5 -- the first percentage in the cell."""
    if v is None or str(v).strip() in missing:
        return None
    m = re.search(r"([\d,\.]+)\s*%", str(v))
    return num(m.group(1), decimal, missing) if m else None


def text(v):
    """A trimmed string, quotes stripped. '' when absent — never None."""
    return "" if v is None else str(v).strip().strip('"')


# --- Reading a file ----------------------------------------------------------
def strip_bom(s):
    return s.lstrip("﻿") if isinstance(s, str) else s


def read_csv(path):
    """-> (header, rows-as-dicts). No number parsing; that is num()'s job."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        rdr = csv.DictReader(f)
        rdr.fieldnames = [strip_bom(c).strip() for c in (rdr.fieldnames or [])]
        return list(rdr.fieldnames), [dict(r) for r in rdr]


class CsvFile:
    """One CSV, read into memory, with its decimal convention settled.

        f = CsvFile(path, decimal="auto")
        for row in f.rows:
            km = f.num(row.get("Distance"))

    The parse helpers above are all available as methods, pre-bound to this
    file's decimal convention and missing-value vocabulary, so an adapter never
    has to thread those through by hand.
    """

    def __init__(self, path, decimal="auto", missing=MISSING):
        self.path = path
        self.name = os.path.basename(path)
        self.missing = tuple(missing)
        self.header, self.rows = read_csv(path)
        self.decimal = (decimal if decimal in ("comma", "dot")
                        else sniff_decimal(self.rows))

    def __len__(self):
        return len(self.rows)

    # -- values --
    def num(self, v):
        return num(v, self.decimal, self.missing)

    def seconds(self, v, formats=("hms",)):
        return seconds(v, formats, self.decimal, self.missing)

    def minutes(self, v, formats=("hms",)):
        s = self.seconds(v, formats)
        return round(s / 60.0) if s else None

    def strip_unit(self, v):
        return strip_unit(v, self.decimal, self.missing)

    def two_nums(self, v):
        return two_nums(v, self.decimal, self.missing)

    def first_percent(self, v):
        return first_percent(v, self.decimal, self.missing)

    def text(self, v):
        return text(v)

    def date(self, v, formats):
        return parse_date(v, formats)

    def datetime(self, v, formats):
        return parse_dt(v, formats)

    # -- columns --
    def missing_columns(self, cols):
        """Which of these column names aren't in the header. Feed it the
        adapter's column map so a typo is reported, not silently ingested
        as all-nulls."""
        return sorted({v for v in cols if v and v not in self.header})
