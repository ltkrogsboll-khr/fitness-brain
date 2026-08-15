#!/usr/bin/env python3
"""Talk to the coach's LLM over raw HTTP -- stdlib only, no per-provider SDK.

Two request/response *shapes* are implemented here, not a fixed vendor list:

  "anthropic"  the Messages API (api.anthropic.com, or a compatible proxy)
  "openai"     the Chat Completions API -- what OpenAI itself speaks, and
               also Ollama, LM Studio, and most third-party gateways, since
               they all clone this shape rather than inventing their own.

`config.coach.llm.provider` picks the shape; `base_url` picks who answers.
That split is what makes a local model or a gateway a config change instead
of a code change -- same reasoning as the ingest adapter boundary, just for
the chat side of this repo instead of the data side.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

PROVIDER_DEFAULTS = {
    "anthropic": {"base_url": "https://api.anthropic.com",
                  "api_key_env": "ANTHROPIC_API_KEY"},
    "openai": {"base_url": "https://api.openai.com/v1",
               "api_key_env": "OPENAI_API_KEY"},
}

DEFAULT_MODEL = "claude-opus-5"


def resolve(cfg):
    """coach config -> (provider, base_url, model, api_key_env).

    Blank fields fall back to the shape's own default, so setting only
    `model` in config.json (say, to point at a different Anthropic model)
    doesn't require repeating the base_url or key env name too.
    """
    c = cfg["coach"].get("llm", {})
    provider = c.get("provider") or "anthropic"
    d = PROVIDER_DEFAULTS.get(provider, PROVIDER_DEFAULTS["openai"])
    base_url = (c.get("base_url") or d["base_url"]).rstrip("/")
    api_key_env = c.get("api_key_env") or d["api_key_env"]
    model = c.get("model") or DEFAULT_MODEL
    return provider, base_url, model, api_key_env


def _http_error(e: urllib.error.HTTPError) -> str:
    """An HTTPError's body usually carries the provider's own explanation --
    surface that instead of a bare status code, so a bad model name or a
    rejected key reads as a sentence rather than 'HTTP Error 400'."""
    try:
        body = json.loads(e.read().decode("utf-8"))
        msg = (body.get("error") or {}).get("message") or body.get("message")
    except Exception:
        msg = None
    return f"HTTP {e.code}: {msg or e.reason}"


def _sse_events(resp):
    """Yield each SSE payload from a streaming HTTP response.

    Stdlib's urllib gives us the response as an iterable of raw lines, which
    is all SSE actually needs: a run of 'field: value' lines per event,
    blank line terminated. Only 'data:' is used by either provider here.
    """
    for raw in resp:
        line = raw.decode("utf-8").rstrip("\n").rstrip("\r")
        if line.startswith("data:"):
            payload = line[len("data:"):].strip()
            if payload and payload != "[DONE]":
                yield payload


def _post(base_url, path, api_key, headers, body):
    req = urllib.request.Request(
        f"{base_url}{path}",
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
        method="POST")
    return urllib.request.urlopen(req, timeout=120)


def stream_anthropic(base_url, api_key, model, system_text, messages, emit):
    """POST /v1/messages, stream=true. Returns (text, stop_reason, in, out)."""
    headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01",
               "content-type": "application/json"}
    body = {
        "model": model,
        "max_tokens": 16000,
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": "high"},
        # Stable prefix across turns -- cache it, same as the SDK call did.
        "system": [{"type": "text", "text": system_text,
                    "cache_control": {"type": "ephemeral"}}],
        "messages": messages,
        "stream": True,
    }
    text_parts, stop_reason, usage_in, usage_out = [], None, 0, 0
    try:
        with _post(base_url, "/v1/messages", api_key, headers, body) as resp:
            for payload in _sse_events(resp):
                ev = json.loads(payload)
                t = ev.get("type")
                if t == "content_block_delta":
                    d = ev.get("delta", {})
                    if d.get("type") == "text_delta":
                        chunk = d.get("text", "")
                        text_parts.append(chunk)
                        emit(chunk)
                elif t == "message_start":
                    usage_in = ev.get("message", {}).get("usage", {}) \
                                 .get("input_tokens", 0)
                elif t == "message_delta":
                    usage_out = ev.get("usage", {}) \
                                  .get("output_tokens", usage_out)
                    stop_reason = ev.get("delta", {}).get("stop_reason",
                                                           stop_reason)
                elif t == "error":
                    raise RuntimeError(ev.get("error", {}).get("message",
                                                                "stream error"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(_http_error(e)) from e
    return "".join(text_parts), stop_reason, usage_in, usage_out


def stream_openai(base_url, api_key, model, system_text, messages, emit):
    """POST /chat/completions, stream=true. Returns (text, stop_reason, in, out)."""
    headers = {"Authorization": f"Bearer {api_key}",
               "content-type": "application/json"}
    oa_messages = [{"role": "system", "content": system_text}] + [
        {"role": m["role"], "content": m["content"]} for m in messages]
    body = {
        "model": model,
        "messages": oa_messages,
        "stream": True,
        # Not every compatible server honours this; usage just stays 0 if not.
        "stream_options": {"include_usage": True},
    }
    text_parts, stop_reason, usage_in, usage_out = [], None, 0, 0
    try:
        with _post(base_url, "/chat/completions", api_key, headers, body) as resp:
            for payload in _sse_events(resp):
                ev = json.loads(payload)
                for choice in ev.get("choices") or []:
                    chunk = (choice.get("delta") or {}).get("content")
                    if chunk:
                        text_parts.append(chunk)
                        emit(chunk)
                    if choice.get("finish_reason"):
                        stop_reason = choice["finish_reason"]
                if ev.get("usage"):
                    usage_in = ev["usage"].get("prompt_tokens", usage_in)
                    usage_out = ev["usage"].get("completion_tokens", usage_out)
    except urllib.error.HTTPError as e:
        raise RuntimeError(_http_error(e)) from e
    return "".join(text_parts), stop_reason, usage_in, usage_out


STREAM = {"anthropic": stream_anthropic, "openai": stream_openai}

# Refusal vocabulary differs per shape -- Anthropic's stop_reason vs OpenAI's
# finish_reason -- but both land here as the same string field, so one set
# covers whichever shape answered.
REFUSAL_REASONS = {"refusal", "content_filter"}


def check_key(provider, base_url, api_key, model):
    """One cheap, non-streaming call -- used only by --set-key to confirm a
    pasted key actually works before it's written to the Keychain."""
    if provider == "anthropic":
        headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01",
                   "content-type": "application/json"}
        body = {"model": model, "max_tokens": 8,
                "messages": [{"role": "user", "content": "hi"}]}
        path = "/v1/messages"
    else:
        headers = {"Authorization": f"Bearer {api_key}",
                   "content-type": "application/json"}
        body = {"model": model, "max_tokens": 8,
                "messages": [{"role": "user", "content": "hi"}]}
        path = "/chat/completions"
    try:
        with _post(base_url, path, api_key, headers, body) as resp:
            resp.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(_http_error(e)) from e
