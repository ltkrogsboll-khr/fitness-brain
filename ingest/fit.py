#!/usr/bin/env python3
"""Reading a .fit file — the binary export behind a single activity.

`parsers.py` handles the CSV a watch vendor emails you; this handles the file
the watch actually wrote. The difference that matters is not the field list —
Garmin's Activities.csv already carries cadence, stride, GCT and power — but the
*resolution*. A CSV row is one average per session. A .fit file is one sample
per second, so it can answer questions an average structurally cannot:

    "my cadence averaged 154" is a session summary.
    "I held 171 for sixteen minutes, then walked home" is what happened.

Both sentences describe the same run. Only the second one is useful when the
walk home is 20% of the elapsed time.

Nothing here is Garmin-specific: FIT is an ANT+ standard and this decodes the
container. What *is* vendor-flavoured is PROFILE below — the field numbers are
global, but which ones a given watch bothers to record is not.

No third-party dependency on purpose. The FIT container is a
definition-message/data-message protocol and decoding it is ~150 lines; the
rest of this repo runs on the standard library and this file keeps that true.

    from ingest.fit import Activity
    a = Activity("23910569221_ACTIVITY.fit")
    a.start_local           -> datetime, wall-clock, as the CSV would write it
    a.session["avg_hr"]     -> 145
    a.records[0]["heart_rate"]
"""

from __future__ import annotations

import os
import struct
from datetime import datetime, timedelta

# FIT counts seconds from this instant, UTC.
FIT_EPOCH = datetime(1989, 12, 31)

# Base type number (the low 5 bits of the base-type byte) ->
# (struct char, size, the value meaning "no reading").
# The invalid value is per-type and always the largest representable one, except
# for the z-types where it is zero -- that is the entire point of a z-type.
BASE = {
    0:  ("B", 1, 0xFF),                  # enum
    1:  ("b", 1, 0x7F),                  # sint8
    2:  ("B", 1, 0xFF),                  # uint8
    3:  ("h", 2, 0x7FFF),                # sint16
    4:  ("H", 2, 0xFFFF),                # uint16
    5:  ("i", 4, 0x7FFFFFFF),            # sint32
    6:  ("I", 4, 0xFFFFFFFF),            # uint32
    7:  ("s", 1, 0x00),                  # string
    8:  ("f", 4, None),                  # float32
    9:  ("d", 8, None),                  # float64
    10: ("B", 1, 0x00),                  # uint8z
    11: ("H", 2, 0x00),                  # uint16z
    12: ("I", 4, 0x00),                  # uint32z
    13: ("B", 1, 0xFF),                  # byte
    14: ("q", 8, 0x7FFFFFFFFFFFFFFF),    # sint64
    15: ("Q", 8, 0xFFFFFFFFFFFFFFFF),    # uint64
    16: ("Q", 8, 0x00),                  # uint64z
}

# Global message numbers we read. Everything else in the file -- and a Garmin
# file has plenty, most of it undocumented telemetry -- is decoded and dropped.
FILE_ID, SESSION, LAP, RECORD, EVENT, ACTIVITY = 0, 18, 19, 20, 21, 34

# field number -> (name, scale, offset). value = raw / scale - offset.
# Field numbers are fixed by the FIT profile and are the same on every device;
# only which ones are *present* varies.
PROFILE = {
    SESSION: {
        253: ("timestamp", 1, 0), 2: ("start_time", 1, 0),
        5: ("sport", 1, 0), 6: ("sub_sport", 1, 0),
        7: ("total_elapsed_time", 1000, 0),   # s
        8: ("total_timer_time", 1000, 0),     # s, excludes auto-pause
        9: ("total_distance", 100, 0),        # m
        11: ("total_calories", 1, 0),
        16: ("avg_hr", 1, 0), 17: ("max_hr", 1, 0),
        18: ("avg_cadence", 1, 0), 19: ("max_cadence", 1, 0),
        20: ("avg_power", 1, 0), 21: ("max_power", 1, 0),
        22: ("total_ascent", 1, 0), 23: ("total_descent", 1, 0),
        24: ("total_training_effect", 10, 0),
        26: ("num_laps", 1, 0),
        89: ("avg_vertical_oscillation", 10, 0),   # mm
        91: ("avg_stance_time", 10, 0),            # ms
        92: ("avg_fractional_cadence", 128, 0),
        # The "enhanced" speed fields are the 32-bit replacements for 14/15.
        # Modern watches fill these and leave the originals empty.
        124: ("avg_speed", 1000, 0),               # m/s
        125: ("max_speed", 1000, 0),               # m/s
        132: ("avg_vertical_ratio", 100, 0),       # %
        133: ("avg_stance_time_balance", 100, 0),  # % left
        134: ("avg_step_length", 10, 0),           # mm
        137: ("total_anaerobic_effect", 10, 0),
    },
    LAP: {
        253: ("timestamp", 1, 0), 254: ("message_index", 1, 0),
        2: ("start_time", 1, 0),
        7: ("total_elapsed_time", 1000, 0),
        8: ("total_timer_time", 1000, 0),
        9: ("total_distance", 100, 0),
        11: ("total_calories", 1, 0),
        15: ("avg_hr", 1, 0), 16: ("max_hr", 1, 0),
        17: ("avg_cadence", 1, 0), 18: ("max_cadence", 1, 0),
        19: ("avg_power", 1, 0), 20: ("max_power", 1, 0),
        21: ("total_ascent", 1, 0), 22: ("total_descent", 1, 0),
        110: ("avg_speed", 1000, 0), 111: ("max_speed", 1000, 0),
    },
    RECORD: {
        253: ("timestamp", 1, 0),
        0: ("position_lat", 1, 0), 1: ("position_long", 1, 0),
        3: ("heart_rate", 1, 0),
        4: ("cadence", 1, 0),                 # rpm; see cadence_spm()
        5: ("distance", 100, 0),              # m
        7: ("power", 1, 0),
        39: ("vertical_oscillation", 10, 0),  # mm
        41: ("stance_time", 10, 0),           # ms
        53: ("fractional_cadence", 128, 0),
        73: ("speed", 1000, 0),               # m/s (enhanced)
        78: ("altitude", 5, 500),             # m (enhanced)
        83: ("vertical_ratio", 100, 0),       # %
        84: ("stance_time_balance", 100, 0),  # % left
        85: ("step_length", 10, 0),           # mm
    },
    ACTIVITY: {
        253: ("timestamp", 1, 0), 5: ("local_timestamp", 1, 0),
        1: ("num_sessions", 1, 0),
    },
    FILE_ID: {
        0: ("type", 1, 0), 1: ("manufacturer", 1, 0), 2: ("product", 1, 0),
        3: ("serial_number", 1, 0), 4: ("time_created", 1, 0),
    },
}

# sport, sub_sport -> the name your CSV export uses for the same activity.
# This matters more than it looks: the type string is half the dedupe key into
# data/sessions.csv, and it selects the config.mechanical weight. Getting it
# wrong doesn't error, it silently creates a second session. Override per sport
# with config.source.options.fit_sport_names.
SPORTS = {
    (1, 0): "Running", (1, 1): "Treadmill Running", (1, 3): "Trail Running",
    (2, 0): "Cycling", (2, 6): "Indoor Cycling",
    (5, 17): "Lap Swimming", (5, 18): "Open Water Swimming",
    (11, 0): "Walking", (11, 3): "Hiking",
    (4, 0): "Strength", (10, 0): "Training", (0, 0): "Other",
}
SPORT_FALLBACK = {1: "Running", 2: "Cycling", 5: "Swimming", 11: "Walking",
                  4: "Strength", 17: "Hiking"}


class FitError(Exception):
    """Not a FIT file, or truncated past the point of being readable."""


# --- The container -----------------------------------------------------------
def decode(path):
    """-> [(global_message_number, {field_number: value}), ...] in file order.

    Values are raw: no scaling, no field names. Invalid values are dropped to
    None here, because "0xFFFF" means "no reading" and letting that reach
    arithmetic is how you get a 65535 bpm heart rate.
    """
    try:
        with open(path, "rb") as f:
            buf = f.read()
    except OSError as e:
        raise FitError(f"{path}: {e}") from e
    if len(buf) < 14 or buf[8:12] != b".FIT":
        raise FitError(f"{os.path.basename(path)} is not a FIT file "
                       f"(no '.FIT' signature at byte 8)")

    header_size = buf[0]
    data_size = struct.unpack("<I", buf[4:8])[0]
    pos, end = header_size, min(header_size + data_size, len(buf))

    defs, out, last_ts = {}, [], None
    while pos < end:
        header = buf[pos]
        pos += 1

        if header & 0x80:
            # Compressed timestamp header: 5 bits of seconds-since-last, which
            # is how a 1 Hz stream avoids spending 4 bytes per second on a clock.
            local = (header >> 5) & 0x03
            d = defs.get(local)
            if not d:
                break
            vals, pos = _read_data(buf, pos, d)
            if last_ts is not None:
                offset, prev = header & 0x1F, last_ts & 0x1F
                last_ts += offset - prev + (32 if offset < prev else 0)
                vals.setdefault(253, last_ts)
            out.append((d["global"], vals))
            continue

        local = header & 0x0F
        if header & 0x40:
            pos = _read_definition(buf, pos, header, local, defs)
            continue

        d = defs.get(local)
        if not d:
            break  # data for a message we never saw defined: the file is cut
        vals, pos = _read_data(buf, pos, d)
        if vals.get(253) is not None:
            last_ts = vals[253]
        out.append((d["global"], vals))
    return out


def _read_definition(buf, pos, header, local, defs):
    pos += 1                                   # reserved byte
    arch = buf[pos]
    pos += 1
    e = ">" if arch else "<"
    gnum = struct.unpack(e + "H", buf[pos:pos + 2])[0]
    pos += 2
    n = buf[pos]
    pos += 1
    fields = []
    for _ in range(n):
        fields.append((buf[pos], buf[pos + 1], buf[pos + 2] & 0x1F))
        pos += 3
    dev = []
    if header & 0x20:
        # Developer fields: third-party data (Stryd power, a custom app). We
        # skip the payload but must know its width to stay byte-aligned.
        nd = buf[pos]
        pos += 1
        for _ in range(nd):
            dev.append((buf[pos], buf[pos + 1], buf[pos + 2]))
            pos += 3
    defs[local] = {"global": gnum, "endian": e, "fields": fields, "dev": dev}
    return pos


def _read_data(buf, pos, d):
    e, vals = d["endian"], {}
    for fnum, size, btype in d["fields"]:
        ch, bsize, invalid = BASE.get(btype, ("B", 1, 0xFF))
        raw = buf[pos:pos + size]
        pos += size
        if ch == "s":
            s = raw.split(b"\x00")[0].decode("utf-8", "replace").strip()
            vals[fnum] = s or None
            continue
        count = max(1, size // bsize)
        try:
            got = struct.unpack(e + ch * count, raw[:bsize * count])
        except struct.error:
            vals[fnum] = None
            continue
        good = [v for v in got if invalid is None or v != invalid]
        vals[fnum] = good[0] if len(good) == 1 else (good or None)
    for _fnum, size, _idx in d["dev"]:
        pos += size
    return vals, pos


def named(global_num, raw):
    """Raw field numbers -> named, scaled values for the messages in PROFILE."""
    prof = PROFILE.get(global_num, {})
    out = {}
    for fnum, v in raw.items():
        if fnum not in prof or v is None or isinstance(v, list):
            continue
        name, scale, offset = prof[fnum]
        out[name] = (v / scale - offset) if scale != 1 or offset else v
    return out


def when(seconds, offset=0):
    """FIT seconds -> datetime, shifted by `offset` seconds of UTC->local."""
    if seconds is None:
        return None
    return FIT_EPOCH + timedelta(seconds=seconds + offset)


def cadence_spm(rec, running=True):
    """Steps per minute from a record.

    FIT stores cadence as *revolutions*, so a runner's 170 spm is recorded as
    85. Doubling it is correct for running and wrong for cycling, which is why
    this needs to be told which it is.
    """
    c = rec.get("cadence")
    if c is None:
        return None
    c += rec.get("fractional_cadence") or 0
    return c * 2 if running else c


# --- One activity ------------------------------------------------------------
class Activity:
    """A decoded .fit file, in the shapes the rest of the repo wants.

    Timestamps are the fiddly part. FIT stores UTC; every CSV export, and
    therefore every key in data/sessions.csv, is local wall-clock. The activity
    message carries both, so the offset is read from the file rather than
    guessed from the current machine's timezone -- which would be wrong for any
    run you did on holiday.
    """

    def __init__(self, path, sport_names=None):
        self.path = path
        self.name = os.path.basename(path)
        msgs = decode(path)

        self._raw_session = next((v for g, v in msgs if g == SESSION), None)
        if self._raw_session is None:
            raise FitError(f"{self.name} has no session message — it may be a "
                           f"settings or monitoring file rather than an activity")

        self.session = named(SESSION, self._raw_session)
        self.laps = [named(LAP, v) for g, v in msgs if g == LAP]
        self.records = [named(RECORD, v) for g, v in msgs if g == RECORD]
        self.file_id = named(FILE_ID, next((v for g, v in msgs if g == FILE_ID), {}))

        act = named(ACTIVITY, next((v for g, v in msgs if g == ACTIVITY), {}))
        ts, local = act.get("timestamp"), act.get("local_timestamp")
        self.utc_offset_s = int(local - ts) if (ts and local) else 0

        self.sport = int(self.session.get("sport") or 0)
        self.sub_sport = int(self.session.get("sub_sport") or 0)
        names = dict(SPORTS)
        names.update({tuple(k) if isinstance(k, (list, tuple)) else k: v
                      for k, v in (sport_names or {}).items()})
        self.type = (names.get((self.sport, self.sub_sport))
                     or names.get(str(self.sport))
                     or SPORT_FALLBACK.get(self.sport)
                     or "Other")
        # Two different questions. `running` asks whether cadence is counted in
        # revolutions and so needs doubling -- true of anything on foot.
        # `is_run` asks whether a cadence floor can separate running from
        # walking, which only makes sense when the activity is a run: a walk's
        # own cadence sits right where that floor goes.
        self.running = self.sport in (1, 11, 17)
        self.is_run = self.sport == 1

    @property
    def start_local(self):
        """Session start as wall-clock — the half of the sessions.csv key."""
        return when(self.session.get("start_time"), self.utc_offset_s)

    @property
    def title(self):
        return self.file_id.get("name") or ""

    def stream(self, field):
        """[(seconds_from_start, value)] for one record field, gaps dropped."""
        t0 = self.session.get("start_time")
        out = []
        for r in self.records:
            ts, v = r.get("timestamp"), r.get(field)
            if ts is None or v is None:
                continue
            out.append((ts - t0, v))
        return out

    def cadence_stream(self):
        t0 = self.session.get("start_time")
        out = []
        for r in self.records:
            ts, c = r.get("timestamp"), cadence_spm(r, self.running)
            if ts is not None and c is not None:
                out.append((ts - t0, c))
        return out

    def __repr__(self):
        return (f"Activity({self.name}, {self.type}, {self.start_local}, "
                f"{len(self.records)} records, {len(self.laps)} laps)")
