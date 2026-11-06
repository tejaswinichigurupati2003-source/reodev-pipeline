"""Hunter.io Domain Search client, with a permanent per-domain cache.

Docs checked (https://hunter.io/api-documentation/v2#domain-search):
    GET https://api.hunter.io/v2/domain-search?domain=...   key in header X-API-KEY
    Free plan: limit + offset must be <= 10, so we ask for limit=10.
    Errors: 401 bad key | 403 per-second rate limit (retry) | 429 monthly usage used up (do NOT retry)
            451 person asked not to be processed | 400 bad params | 5xx server error (retry)
The free tier only has about 50 credits a month (25 in the original brief), so a domain is NEVER searched twice:
the answer is saved in data/<domain>/hunter_raw.json and reused forever (even with --fresh).
Delete that file by hand if you really want to spend another search.
"""
import os
import time

import requests
from dotenv import load_dotenv

import pipeline_io as io

load_dotenv()

URL = "https://api.hunter.io/v2/domain-search"
CACHE_FILE = "hunter_raw.json"
LIMIT = 10                      # free-plan maximum (limit + offset <= 10)
MAX_RETRIES = 2
BACKOFF_SECONDS = (2, 5)
RETRYABLE_STATUS = {403, 500, 502, 503, 504}  # 403 = Hunter's per-second rate limit
_sleep = time.sleep             # tests replace this


class HunterError(Exception):
    """Raised for problems retrying cannot fix (bad key, quota used up ...)."""


def _request(domain):
    """The ONLY function that calls Hunter (tests replace it). Returns (status_code, json_dict)."""
    key = os.getenv("HUNTER_API_KEY")
    if not key:
        raise HunterError("HUNTER_API_KEY is not set (add it to .env)")
    resp = requests.get(
        URL,
        params={"domain": domain, "limit": LIMIT, "type": "personal"},  # personal = skip info@ style addresses
        headers={"X-API-KEY": key},  # header, so the key never appears in a URL or log
        timeout=30,
    )
    try:
        body = resp.json()
    except ValueError:
        body = {}
    return resp.status_code, body


def domain_search(domain):
    """Return Hunter's response for `domain`. Uses the cache if it exists (no credit spent)."""
    if io.exists(domain, CACHE_FILE):
        return io.load_json(domain, CACHE_FILE)

    last_problem = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            status, body = _request(domain)
        except requests.RequestException as exc:  # network trouble: retry
            status, body, last_problem = None, {}, f"network error: {exc}"
        if status == 200:
            io.save_json(domain, CACHE_FILE, body)  # also caches "no results", so we never re-spend
            return body
        if status == 401:
            raise HunterError("Hunter rejected the API key (401). Check HUNTER_API_KEY.")
        if status == 429:
            raise HunterError("Hunter usage limit reached (429): the monthly searches are used up.")
        if status == 451:
            raise HunterError("Hunter cannot process this domain (451, legal request).")
        if status == 400:
            raise HunterError(f"Hunter rejected the request (400): {body.get('errors')}")
        if status is not None:
            last_problem = f"HTTP {status}"
        if attempt < MAX_RETRIES:
            _sleep(BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)])
    raise HunterError(f"Hunter lookup failed after {MAX_RETRIES + 1} attempts ({last_problem})")
