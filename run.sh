#!/usr/bin/env bash
# Start the training dashboard. Chat needs an API key for whatever LLM
# config.coach.llm points at -- see serve.py --set-key.
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
./.venv/bin/python build.py
exec ./.venv/bin/python -u serve.py
