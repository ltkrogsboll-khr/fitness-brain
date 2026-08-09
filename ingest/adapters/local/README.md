# Your adapters

Drop `.py` files here and they show up in `python3 -m ingest`. Select one with
`"source": { "adapter": "yourfile" }` in `config.json`.

**Upstream never adds files to this folder.** That is the whole point of it:
you can commit your adapter, and `git pull` will still fast-forward cleanly
because no upstream commit ever touches a path under `local/`.

Prefer to keep it out of git entirely? Add it to `.git/info/exclude` rather
than `.gitignore` — same effect, but it's a local file, so you're not carrying
a modification to a tracked one forever:

    echo 'ingest/adapters/local/*.py' >> .git/info/exclude

A file here **shadows a shipped adapter of the same name**. So if `garmin_csv`
is nearly right for you, copy it to `local/garmin_csv.py` and bend it there:
your config keeps working, upstream keeps updating its copy, and nothing
conflicts.

To write one: `cp ../_template.py mysource.py`, or ask your agent — the repo
ships a skill for it at `.claude/skills/ingest-adapter/`. The contract is
`../../README.md`, the field list is `../../schema.py`.

If your adapter works, consider opening a PR to move it up into
`ingest/adapters/` — the next person with that watch gets it for free.
