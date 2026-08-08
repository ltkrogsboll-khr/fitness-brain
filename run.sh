#!/usr/bin/env bash
# Start the training dashboard. Chat needs ANTHROPIC_API_KEY exported.
cd "$(dirname "$0")"
[ -d .venv ] || { python3 -m venv .venv && ./.venv/bin/pip install -q anthropic; }
./.venv/bin/python build.py
exec ./.venv/bin/python -u serve.py
