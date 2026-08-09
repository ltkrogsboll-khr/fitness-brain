#!/usr/bin/env python3
"""Starting point for a new adapter. Copy, don't edit in place.

    cp ingest/adapters/_template.py ingest/adapters/local/mysource.py

Filenames starting with `_` are skipped by adapter discovery, which is what
keeps this template out of the list.

Then in config.json:

    "source": { "adapter": "mysource" }

and check it with `python3 -m ingest --check`, which runs the adapter and
prints how many records carried each field — without writing anything.

The full contract is ingest/README.md; the field list with units is
ingest/schema.py. The short version: return sessions, sleep and hrv records
with the field names below. Omit whatever your source doesn't have. Everything
downstream — TRIMP, ACWR, the dashboard, the coach — reads only these.
"""

from __future__ import annotations

from ingest import Ingested
from ingest import parsers as p

CONTRACT = 1
DESCRIPTION = "one line, shown by `python3 -m ingest`"


def hint(cfg):
    """Optional. Printed when data/raw/ is empty — say what to put there."""
    return "Export <what> from <where> into data/raw/."


def ingest(raw_dir, cfg, report):
    """raw_dir: absolute path to data/raw/
    cfg:       the merged config (config.example.json over config.py DEFAULTS)
    report:    say what you read and what you skipped — see ingest/report.py

    -> Ingested(sessions=[...], sleep={date: {...}}, hrv={date: {...}})
    """
    src = cfg["source"]
    out = Ingested()

    # Sort data/raw/ into kinds by filename substring. Your adapter is free to
    # ignore this and glob for its own filenames instead.
    files = p.route_files(raw_dir, src["files"], report)

    for path in files.get("activities", []):
        # CsvFile settles the decimal convention for this file once, then every
        # number you pull out of it is parsed that way.
        f = p.CsvFile(path, decimal=src["decimal"], missing=src["missing"])

        # Name a column that isn't in the header and you get a silent column of
        # nulls. This turns that into a line of output.
        report.missing_columns("activities", f, ["Date", "Type", "Distance"])

        rows, bad = [], []
        for r in f.rows:
            dt = f.datetime(r.get("Date"), src["datetime_formats"])
            if dt is None:
                bad.append(f.text(r.get("Date")))
                continue
            rows.append({
                # Required. datetime + type is the dedupe key across re-exports,
                # so it has to be stable: same workout, same string, every time.
                "datetime": dt.isoformat(sep=" "),
                "date": dt.date().isoformat(),
                "type": f.text(r.get("Type")),
                # Optional from here down. Units matter — see schema.py.
                "distance_km": f.num(r.get("Distance")),
                "duration_s": f.seconds(r.get("Time"), ("hms",)),
                "avg_hr": f.num(r.get("Avg HR")),
                # Extra fields ride through into data/sessions.csv and can be
                # named as config.form_metric.field:
                # "power_w": f.num(r.get("Avg Power")),
            })

        report.skipped("activities", bad, "timestamp",
                       "add its format to config.source.datetime_formats")
        report.file(f.name, "activities", len(f), len(rows), f.decimal)
        out.sessions += rows

    # Sleep and HRV are dicts keyed by 'YYYY-MM-DD'. Return {} for either one
    # if your source doesn't export it — the engine handles absent signals.
    #
    # for path in files.get("sleep", []):
    #     f = p.CsvFile(path, decimal=src["decimal"], missing=src["missing"])
    #     for r in f.rows:
    #         d = f.date(r.get("Date"), src["date_formats"])
    #         if d:
    #             out.sleep[d.isoformat()] = {
    #                 "date": d.isoformat(),
    #                 "rhr": f.num(r.get("Resting HR")),
    #                 "sleep_min": f.minutes(r.get("Duration"), ("hm_text",)),
    #             }

    return out
