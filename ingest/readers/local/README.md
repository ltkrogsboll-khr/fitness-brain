# Your readers

Single-session files — one file, one workout, sampled second by second. Drop
`.py` files here and they show up in `python3 -m ingest --readers`.

There is **nothing to select**. Readers are claimed by file extension, not
named in config: declare `EXTENSIONS = (".tcx",)` in your file and every `.tcx`
in `data/activities/` starts going through it. That's the difference from
`../../adapters/local/`, where one adapter owns the whole bulk export and
`config.source.adapter` picks it.

**Upstream never adds files to this folder.** That is the whole point of it:
you can commit your reader, and `git pull` will still fast-forward cleanly
because no upstream commit ever touches a path under `local/`.

Prefer to keep it out of git entirely? Add it to `.git/info/exclude` rather
than `.gitignore` — same effect, but it's a local file, so you're not carrying
a modification to a tracked one forever:

    echo 'ingest/readers/local/*.py' >> .git/info/exclude

A file here **shadows a shipped reader of the same name**. So if `fit` is
nearly right for you — an odd sport mapping, a device writing a field somewhere
non-standard — copy it to `local/fit.py` and bend it there: upstream keeps
updating its copy, and nothing conflicts.

To write one: `cp ../_template.py mysource.py`, or ask your agent — the repo
ships a skill for it at `.claude/skills/ingest-adapter/`. The contract, with
units per field, is `../../activity.py`.

Watch the two things that actually go wrong: `start_local` must be **local wall
clock** (it is half the key into `data/sessions.csv`, so a UTC timestamp
silently creates a duplicate session and doubles that day's load), and cadence
must be in the sport's own unit — double it in the reader if your format counts
a runner's 170 spm as 85.

If your reader works, consider opening a PR to move it up into
`ingest/readers/` — the next person with that format gets it for free.
