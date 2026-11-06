"""Apollo.io client: find people at a company, then reveal emails for only the few we want.

Docs checked (docs.apollo.io, Nov 2026):
    Search   POST https://api.apollo.io/api/v1/mixed_people/api_search   0 credits, returns NO emails
             params (query string): q_organization_domains_list[], person_titles[], person_seniorities[], page, per_page
             people: id, first_name, last_name_obfuscated, title, has_email, organization{name}
    Enrich   POST https://api.apollo.io/api/v1/people/bulk_match         up to 10 people, JSON {"details": [...]}
             1 credit per person when an email is found. matches[]: id, first_name, last_name, title, email,
             email_status, linkedin_url, organization
    Auth     header x-api-key.   Free plan limits are low (search 50/min, 200/hour, 600/day) and a free account
             may not be allowed to use the API (403 API_INACCESSIBLE) - then pipeline falls back to Hunter.
    Errors   401 bad key | 403 endpoint not allowed for this plan | 422 bad params | 429 rate limit (retry-after header)
Because search is free but emails cost credits, we search first, pick the best people by title in code,
and enrich only those (at most MAX_ENRICH per domain). Every answer is cached in data/<domain>/apollo_raw.json,
so a domain is never searched or enriched twice.
"""
import os
import time

import requests
from dotenv import load_dotenv

import pipeline_io as io

load_dotenv()

BASE = "https://api.apollo.io/api/v1"
CACHE_FILE = "apollo_raw.json"
MAX_ENRICH = 3
MAX_RETRIES = 2
_sleep = time.sleep  # tests replace this

TITLES = ["head of developer relations", "developer advocate", "developer marketing", "developer experience", "community manager",
          "head of community", "head of growth", "growth marketing", "product marketing", "marketing operations", "revenue operations"]
STATUS_CONFIDENCE = {"verified": 95, "likely to engage": 80, "extrapolated": 60, "unavailable": 20}


class ApolloError(Exception):
    """Problems retrying cannot fix (bad key, plan not allowed, quota)."""


def _request(method, path, params=None, json=None):
    """The ONLY function that calls Apollo (tests replace it). Returns (status_code, json_dict, headers)."""
    key = os.getenv("APOLLO_API_KEY")
    if not key:
        raise ApolloError("APOLLO_API_KEY is not set (add it to .env)")
    resp = requests.request(method, BASE + path, params=params, json=json, timeout=60,
                            headers={"x-api-key": key, "Content-Type": "application/json", "Cache-Control": "no-cache"})
    try:
        body = resp.json()
    except ValueError:
        body = {}
    return resp.status_code, body, resp.headers


def _call(method, path, params=None, json=None):
    last = None
    for attempt in range(MAX_RETRIES + 1):
        status, body, headers = _request(method, path, params, json)
        if status == 200:
            return body
        if status == 401:
            raise ApolloError("Apollo rejected the API key (401). Check APOLLO_API_KEY.")
        if status == 403:
            raise ApolloError("Apollo says this plan/key cannot use that endpoint (403). Falling back is fine.")
        if status in (400, 422):
            raise ApolloError(f"Apollo rejected the request ({status}): {body}")
        if status == 429:
            window = ((body.get("error_details") or {}).get("context") or {}).get("window", {}).get("value")
            if window in ("hour", "day"):  # waiting a minute will not help
                raise ApolloError(f"Apollo rate limit reached for the {window} (429).")
            wait = min(float(headers.get("retry-after") or 60), 60)
        else:
            wait = 3 * (attempt + 1)
        last = f"HTTP {status}"
        if attempt < MAX_RETRIES:
            _sleep(wait)
    raise ApolloError(f"Apollo call failed after {MAX_RETRIES + 1} attempts ({last})")


def search(domain):
    return _call("POST", "/mixed_people/api_search", params={
        "q_organization_domains_list[]": [domain], "person_titles[]": TITLES,
        "person_seniorities[]": ["founder", "c_suite", "vp", "head", "director", "manager", "senior"],
        "page": 1, "per_page": 25})


def enrich(people):
    """Reveal emails for the chosen people (costs credits). `people` = search results (need `id`)."""
    if not people:
        return []
    details = [{"id": p["id"], "first_name": p.get("first_name"), "organization_name": (p.get("organization") or {}).get("name")}
               for p in people]
    body = _call("POST", "/people/bulk_match", params={"reveal_personal_emails": "false"}, json={"details": details})
    return [m for m in (body.get("matches") or []) if m]


def find_people(domain, want):
    """Return people shaped like Hunter's `emails` list: value, first_name, last_name, position, confidence, linkedin.
    `want(title)` returns a sort key (lower = better) for titles we want, or None to skip them.
    Cached forever per domain (even when nothing was found)."""
    if io.exists(domain, CACHE_FILE):
        raw = io.load_json(domain, CACHE_FILE)
    else:
        found = search(domain).get("people") or []
        picked = sorted((p for p in found if p.get("has_email") and want(p.get("title")) is not None),
                        key=lambda p: want(p.get("title")))[:MAX_ENRICH]
        raw = {"domain": domain, "search_total": len(found), "enriched": enrich(picked)}
        io.save_json(domain, CACHE_FILE, raw)
    out = []
    for m in raw.get("enriched", []):
        if m.get("email"):
            out.append({"value": m["email"], "first_name": m.get("first_name"), "last_name": m.get("last_name"),
                        "position": m.get("title"), "linkedin": m.get("linkedin_url"),
                        "confidence": STATUS_CONFIDENCE.get((m.get("email_status") or "").lower(), 50)})
    return out
