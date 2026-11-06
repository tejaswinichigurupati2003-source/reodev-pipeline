"""LLM helper used by steps 2, 3, 4, 5, 6. Talks to OpenRouter (which serves Gemini models).

Docs checked (https://openrouter.ai/docs):
    POST https://openrouter.ai/api/v1/chat/completions
    header  Authorization: Bearer <OPENROUTER_API_KEY>
    body    {"model", "messages", "temperature", "response_format": {"type": "json_schema",
             "json_schema": {"name", "strict", "schema"}}, "provider": {"require_parameters": true}}
    answer  choices[0].message.content
    errors  {"error": {"code": <http status>, "message": ...}}; 429 = rate limited, 402 = no credits.
The model id (OPENROUTER_MODEL) looks like "google/gemini-3.8-flash".

Strict schema mode is OFF: the docs do not say whether nullable types like ["string","null"] work
in strict mode. Instead, "require_parameters" makes OpenRouter use only providers that support
JSON schemas, and this file checks the reply has every required field (and retries if not).
All network access goes through _call_model(), so tests replace it.
"""
import json
import os
import re
import time

import requests
from dotenv import load_dotenv

load_dotenv()

URL = "https://openrouter.ai/api/v1/chat/completions"
MAX_RETRIES = 2                      # = 3 attempts in total
RETRYABLE_CODES = {408, 429, 500, 502, 503, 504}
BACKOFF_SECONDS = (2, 5)             # normal errors: wait 2s, then 5s
RATE_LIMIT_BACKOFF_SECONDS = (15, 40)  # 429 rate limit: wait longer
MAX_RETRY_AFTER = 60                 # never wait longer than this, even if the server asks
_sleep = time.sleep                  # tests replace this so they do not really wait


class LLMError(Exception):
    """An error from OpenRouter. `code` is the HTTP-style status code (429, 402, 401 ...)."""

    def __init__(self, code, message, retry_after=None):
        super().__init__(f"OpenRouter error {code}: {message}")
        self.code = code
        self.retry_after = retry_after


def _call_model(prompt, schema, system, temperature):
    """The ONLY function that talks to OpenRouter (tests replace it). Returns the raw reply text."""
    key = os.getenv("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY is not set (add it to .env)")
    model = os.getenv("OPENROUTER_MODEL")
    if not model:
        raise RuntimeError("OPENROUTER_MODEL is not set (add it to .env, e.g. google/gemini-3.8-flash)")

    messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
    body = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "response_format": {"type": "json_schema", "json_schema": {"name": "answer", "strict": False, "schema": schema}},
        "provider": {"require_parameters": True},  # only use providers that support json_schema
    }
    resp = requests.post(
        URL, json=body, timeout=120,
        headers={"Authorization": f"Bearer {key}", "X-Title": "Reo.Dev switch-campaign pipeline"},  # X-Title is optional
    )
    try:
        data = resp.json()
    except ValueError:
        data = {}
    if resp.status_code != 200 or data.get("error"):  # some provider failures come back as HTTP 200 + error
        err = data.get("error") or {}
        retry_after = resp.headers.get("Retry-After")
        raise LLMError(err.get("code") if isinstance(err.get("code"), int) else resp.status_code,
                       err.get("message") or resp.text[:200],
                       float(retry_after) if retry_after and retry_after.replace(".", "", 1).isdigit() else None)
    content = ((data.get("choices") or [{}])[0].get("message") or {}).get("content")
    if not content:
        raise ValueError("The model returned no text")
    return content


def _parse_json(text):
    """Parse the reply; some models wrap JSON in ```json fences, so strip those first."""
    text = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", text.strip())
    return json.loads(text)


def _error_code(exc):
    code = getattr(exc, "code", None)
    return code if isinstance(code, int) else None


def generate_json(prompt, schema, system=None, temperature=0.0):
    """Ask the model for JSON matching `schema`. Retries twice with backoff. Returns a dict."""
    last_error = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            result = _parse_json(_call_model(prompt, schema, system, temperature))
            if not isinstance(result, dict):
                raise ValueError("The model did not return a JSON object")
            missing = [k for k in schema.get("required", []) if k not in result]
            if missing:
                raise ValueError(f"The reply is missing required fields: {missing}")
            return result
        except RuntimeError:
            raise  # missing key/model name: retrying cannot help
        except Exception as exc:  # API errors, bad JSON, network problems
            last_error = exc
            code = _error_code(exc)
            if code is not None and code not in RETRYABLE_CODES:
                raise  # e.g. 400 bad request / 401 bad key / 402 no credits: retrying cannot help
            if attempt == MAX_RETRIES:
                break
            delays = RATE_LIMIT_BACKOFF_SECONDS if code == 429 else BACKOFF_SECONDS
            wait = delays[min(attempt, len(delays) - 1)]
            retry_after = getattr(exc, "retry_after", None)
            _sleep(min(retry_after, MAX_RETRY_AFTER) if retry_after else wait)
    raise RuntimeError(f"LLM call failed after {MAX_RETRIES + 1} attempts: {last_error}")
