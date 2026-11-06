"""Prospeo client: find people at a company, then reveal emails for only the few we want.

Docs checked (https://prospeo.io/api-docs/*, Nov 2026):
    Host    https://api.prospeo.io, every endpoint is POST, header X-KEY: <key>, Content-Type: application/json
    Search  POST /search-person  {"filters": {"company": {"websites": {"include": [domain]}},
                                  "person_job_title": {"include": [...], "match_mode": "CONTAINS"},
                                  "person_seniority": {"include": [...]}}, "page": 1}
            25 results per page, NO emails. 1 credit per request that returns at least one person.
            results[].person: person_id, first_name, last_name, current_job_title, linkedin_url
    Enrich  POST /enrich-person  {"only_verified_email": true, "data": {"person_id": "..."}}
            1 credit per email found (a non-match is not charged). person.email: {status, revealed, email}
    Errors  HTTP 200 + "error": false is success. Failures: "error": true + error_code (HTTP 400 mostly):
            INVALID_API_KEY | INSUFFICIENT_CREDITS | NO_RESULTS (search) | NO_MATCH (enrich) | INVALID_FILTERS | 429 rate limit
NOT confirmed in the docs: the free plan's monthly credits, and whether an unmasked email always comes back
(an example shows a masked one). If an email comes back masked we skip it and say so.
Cost per account: 1 search + up to MAX_ENRICH emails. Every answer is cached forever in data/<domain>/prospeo_raw.json.
"""
import os
import time

import requests
from dotenv import load_dotenv

import pipeline_io as io

load_dotenv()

BASE = "https://api.prospeo.io"
CACHE_FILE = "prospeo_raw.json"
MAX_RETRIES = 2
_sleep = time.sleep  # tests replace this

# Research interviews: DevRel, community, developer marketing and growth first, tooling owners second. No sales leaders, founders or executives.
TITLES = ["Developer Relations", "Developer Advocate", "Developer Marketing", "Developer Experience", "Community",
          "Growth", "Product Marketing", "Marketing Operations", "Revenue Operations", "GTM"]
SENIORITY = ["Vice President", "Head", "Director", "Manager", "Senior"]


def max_enrich():
    try:
        return max(1, int(os.getenv("PROSPEO_MAX_ENRICH") or 2))
    except ValueError:
        return 2


class ProspeoError(Exception):
    """Problems retrying cannot fix (bad key, no credits, invalid request)."""


def _request(path, body):
    """The ONLY function that calls Prospeo (tests replace it). Returns (status_code, json_dict)."""
    key = os.getenv("PROSPEO_API_KEY")
    if not key:
        raise ProspeoError("PROSPEO_API_KEY is not set (add it to .env)")
    resp = requests.post(BASE + path, json=body, timeout=60, headers={"X-KEY": key, "Content-Type": "application/json"})
    try:
        data = resp.json()
    except ValueError:
        data = {}
    return resp.status_code, data


def _call(path, body, ok_errors=()):
    """Returns the parsed answer, or {} when the error_code is in ok_errors (a normal 'nothing found')."""
    last = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            status, data = _request(path, body)
        except requests.RequestException as exc:  # network trouble (DNS, timeout): retry like a server error
            status, data, last = None, {}, f"network error: {type(exc).__name__}"
            if attempt < MAX_RETRIES:
                _sleep(3 * (attempt + 1))
            continue
        if status == 200 and not data.get("error"):
            return data
        code = data.get("error_code")
        if code in ok_errors:
            return {}
        if code == "INVALID_API_KEY":
            raise ProspeoError("Prospeo rejected the API key (INVALID_API_KEY). Check PROSPEO_API_KEY.")
        if code == "INSUFFICIENT_CREDITS":
            raise ProspeoError("Prospeo usage limit reached (INSUFFICIENT_CREDITS): credits are used up.")
        if code in ("INVALID_FILTERS", "INVALID_REQUEST", "INVALID_DATAPOINTS", "PLAN_REQUIRED"):
            raise ProspeoError(f"Prospeo rejected the request ({code}): {data.get('filter_error') or ''}")
        last = f"HTTP {status} {code or ''}".strip()
        if attempt < MAX_RETRIES:
            _sleep(5 * (attempt + 1) if status == 429 else 3 * (attempt + 1))
    raise ProspeoError(f"Prospeo call failed after {MAX_RETRIES + 1} attempts ({last})")


def search(domain):
    body = {"page": 1, "filters": {
        "company": {"websites": {"include": [domain]}},
        "person_job_title": {"include": TITLES, "match_mode": "CONTAINS"},
        "person_seniority": {"include": SENIORITY}}}
    return [r.get("person") or {} for r in _call("/search-person", body, ok_errors=("NO_RESULTS",)).get("results", [])]


def enrich(person):
    """Reveal one person's email (1 credit if found). Returns the person dict, or None."""
    body = {"only_verified_email": True, "data": {"person_id": person["person_id"]}}
    return _call("/enrich-person", body, ok_errors=("NO_MATCH",)).get("person")


def enrich_known(full_name, domain):
    """Email for a person we already know by name and company (1 credit if found, none if not).
    Returns {"email", "status", "linkedin_url", "title"} or None."""
    body = {"only_verified_email": True, "data": {"full_name": full_name, "company_website": domain}}
    p = _call("/enrich-person", body, ok_errors=("NO_MATCH",)).get("person") or {}
    e = p.get("email") or {}
    if e.get("email") and "*" not in e["email"]:
        return {"email": e["email"], "status": e.get("status"), "linkedin_url": p.get("linkedin_url"),
                "title": p.get("current_job_title")}
    return None


def find_people(domain, want):
    """Return people shaped like Hunter's `emails` list. `want(title)` -> sort key (lower = better) or None to skip."""
    if io.exists(domain, CACHE_FILE):
        raw = io.load_json(domain, CACHE_FILE)
    else:
        found = search(domain)
        picked = sorted((p for p in found if p.get("person_id") and want(p.get("current_job_title")) is not None),
                        key=lambda p: want(p.get("current_job_title")))[:max_enrich()]
        enriched, masked = [], 0
        for p in picked:
            full = enrich(p)
            email = ((full or {}).get("email") or {})
            if email.get("email") and "*" not in email["email"]:
                enriched.append({**p, "email": email["email"], "email_status": email.get("status")})
            elif email.get("email"):
                masked += 1  # found, but Prospeo returned it masked: not usable
        raw = {"domain": domain, "search_total": len(found), "masked_emails": masked, "enriched": enriched}
        io.save_json(domain, CACHE_FILE, raw)
    return [{"value": m["email"], "first_name": m.get("first_name"), "last_name": m.get("last_name"),
             "position": m.get("current_job_title"), "linkedin": m.get("linkedin_url"),
             "confidence": 95 if (m.get("email_status") or "").upper() == "VERIFIED" else 60}
            for m in raw.get("enriched", [])]
