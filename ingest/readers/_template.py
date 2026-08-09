#!/usr/bin/env python3
"""Starting point for a new single-session reader. Copy, don't edit in place.

    cp ingest/readers/_template.py ingest/readers/local/tcx.py

Filenames starting with `_` are skipped by reader discovery, which is what
keeps this template out of the list.

There is nothing to configure afterwards. Readers are picked by file extension,
so declaring EXTENSIONS below is what puts yours in service — drop a matching
file in data/activities/ and `python3 build.py` will use it.

    python3 -m ingest --readers        check that yours is listed
    python3 analyze.py yourfile.tcx --dry-run    run it, write nothing

The full contract, with units per field, is ingest/activity.py. The short
version: return an Activity whose `records` are one dict per sample. Omit
whatever your format doesn't carry — every field is optional except `t`, and a
missing signal degrades one measure rather than breaking the run.
"""

from __future__ import annotations

from ingest.activity import Activity, ActivityError

CONTRACT = 1
DESCRIPTION = "one line, shown by `python3 -m ingest --readers`"
EXTENSIONS = (".tcx",)      # lower-case, with the dot


def read(path, cfg=None):
    """path: absolute path to one activity file
    cfg:  the merged config, or None. Use cfg["source"]["options"] for
          settings of your own — nothing else in this repo reads that key.

    Raise ActivityError for a file you cannot make sense of; say what is wrong
    with it, because that message is what the user sees.
    """
    records = []
    # for sample in your_parser(path):
    #     records.append({
    #         "t": 12.0,              # REQUIRED: seconds from session start
    #         "heart_rate": 148,      # bpm
    #         "cadence": 170,         # steps/min on foot, rev/min cycling --
    #                                 # DOUBLE IT HERE if your format stores
    #                                 # running cadence in revolutions
    #         "speed": 2.48,          # metres/second
    #         "distance": 415.0,      # metres, cumulative
    #         "altitude": 12.0,       # metres
    #         "power": 260,           # watts
    #     })

    session = {
        # "total_distance": 2756.0,       # metres
        # "total_timer_time": 1228.4,     # seconds
        # "avg_hr": 145, "max_hr": 159,   # bpm
        # "avg_cadence": 154,             # same unit as records
        # "avg_speed": 2.244,             # metres/second
        # "total_ascent": 6,              # metres
        # "total_calories": 209,
    }

    laps = []
    # laps.append({"total_distance": 1000.0, "total_timer_time": 420.9,
    #              "avg_hr": 144, "max_hr": 159, "avg_cadence": 160})

    raise ActivityError("template reader — copy it and write read()")

    return Activity(
        path=path,
        type="Running",        # the vendor's own word, matching your CSV export
        start_local=None,      # REQUIRED: datetime, LOCAL wall clock, no tzinfo
        session=session, laps=laps, records=records,
        is_run=True,           # True only for running: it enables the walk floor
    )
