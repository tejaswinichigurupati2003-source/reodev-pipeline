"""Step 4 - Find up to 3 people to contact per account from Apollo, Prospeo and/or Hunter (nobody is invented).

Output: data/<domain>/step4_contacts.json
Filter (code): first + last name, email, Hunter confidence >= 70, title on the persona list below.
Rank (code): persona tier, then Hunter confidence. Founder/CEO only counts for companies under ~25 people
(or unknown size), because at a bigger company they are not the owner of developer-growth tooling.
Every account also gets a LinkedIn people-search link so a human can find the right person when Hunter has nobody.
We only generate the link; we never scrape LinkedIn.
"""
import json
import os
import re
from urllib.parse import quote

import apollo
import hunter
import prospeo
import users
import pipeline_io as io

OUTPUT_FILE = "step4_contacts.json"
MIN_CONFIDENCE = 70
MAX_CONTACTS = 3
SMALL_COMPANY = 25
FOUNDER_TIER = 4

TIERS = [
    (1, "DevRel / Community / Developer Marketing", re.compile(
        r"developer (?:relations|advocacy|advocate|marketing|experience|community|programs?|growth)|\bdevrel\b|\bdevex\b|"
        r"\bcommunity\b|developer evangelist|open source (?:program|lead|manager)|\bdx\b")),
    (2, "Growth / Demand Gen / Marketing leader", re.compile(
        r"\b(?:head|director|vp|vice president|chief|lead)\b.*\b(?:growth|demand|marketing|cmo|acquisition)\b|\bcmo\b|"
        r"\b(?:growth|demand gen\w*|product marketing)\b.*\b(?:head|director|vp|lead|manager)\b")),
    (3, "RevOps / GTM Ops / Sales leader", re.compile(
        r"\brevops\b|revenue operations|\bgtm\b|go[- ]to[- ]market|sales operations|\bsales ops\b|"
        r"\b(?:head|director|vp|vice president)\b.*\bsales\b|\bcro\b|chief revenue officer")),
    (4, "Founder / CEO", re.compile(r"\bco-?founder\b|\bfounder\b|\bceo\b|chief executive")),
]


def classify(position):
    title = (position or "").lower()
    if not title:
        return None
    for tier, label, pattern in TIERS:
        if pattern.search(title):
            return tier, label
    return None


def headcount_min(text):
    nums = [int(n.replace(",", "")) for n in re.findall(r"\d[\d,]*", text or "")]
    return min(nums) if nums else None


def linkedin_search_url(company, title_hint="developer relations OR community OR growth"):
    return "https://www.linkedin.com/search/results/people/?keywords=" + quote(f"{title_hint} {company}")


def pick_contacts(hunter_emails, headcount):
    hc = headcount_min(headcount)
    small_ok = hc is None or hc < SMALL_COMPANY
    eligible, rejected = [], []
    for e in hunter_emails:
        if not e.get("value"):
            continue
        who = {"email": e["value"], "name": " ".join(filter(None, [e.get("first_name"), e.get("last_name")])) or None,
               "title": e.get("position"), "hunter_confidence": e.get("confidence") or 0,
               "linkedin": e.get("linkedin")}
        tier = classify(e.get("position"))
        reason = None
        if not (e.get("first_name") and e.get("last_name")):
            reason = "no first/last name"
        elif who["hunter_confidence"] < MIN_CONFIDENCE:
            reason = f"Hunter confidence {who['hunter_confidence']} < {MIN_CONFIDENCE}"
        elif tier is None:
            reason = f"title not on persona list ({e.get('position') or 'none'})"
        elif tier[0] == FOUNDER_TIER and not small_ok:
            reason = f"founder/CEO only for companies under ~{SMALL_COMPANY} people (headcount {headcount})"
        if reason:
            rejected.append({**who, "why": reason})
        else:
            eligible.append({**who, "tier": tier[0], "persona": tier[1]})
    eligible.sort(key=lambda c: (c["tier"], -c["hunter_confidence"]))
    return eligible[:MAX_CONTACTS], rejected + eligible[MAX_CONTACTS:]


def is_cached(domain):
    """True if a provider already answered for this domain (so running it again spends nothing)."""
    return any(io.exists(domain, f) for f in (apollo.CACHE_FILE, prospeo.CACHE_FILE, hunter.CACHE_FILE))


def make_want(headcount):
    """title -> sort key for the people worth enriching (None = skip). Applies the founder-size rule."""
    hc = headcount_min(headcount)
    small_ok = hc is None or hc < SMALL_COMPANY

    def want(title):
        tier = classify(title)
        if tier is None or (tier[0] == FOUNDER_TIER and not small_ok):
            return None
        return tier[0]
    return want


def _providers(domain, headcount):
    """(name, api-key variable, cache file, error class, function returning people) in the order they are tried."""
    want = make_want(headcount)
    return [
        ("apollo", "APOLLO_API_KEY", apollo.CACHE_FILE, apollo.ApolloError, lambda: apollo.find_people(domain, want)),
        ("prospeo", "PROSPEO_API_KEY", prospeo.CACHE_FILE, prospeo.ProspeoError, lambda: prospeo.find_people(domain, want)),
        ("hunter", "HUNTER_API_KEY", hunter.CACHE_FILE, hunter.HunterError,
         lambda: (hunter.domain_search(domain).get("data") or {}).get("emails") or []),
    ]


def fetch_people(domain, headcount):
    """Ask the providers in order (Apollo, Prospeo, Hunter), skipping any without a key (or cache).
    CONTACT_PROVIDER = auto (all) | apollo | prospeo | hunter forces one.
    The first provider that returns people wins. Returns (people, source, notes).
    Raises only if every provider that was tried failed."""
    mode = (os.getenv("CONTACT_PROVIDER") or "auto").lower()
    notes, errors, tried = [], [], []
    for name, key_var, cache_file, err_cls, find in _providers(domain, headcount):
        if mode not in ("auto", name):
            continue
        if not (os.getenv(key_var) or io.exists(domain, cache_file) or mode == name):
            continue
        tried.append(name)
        try:
            people = find()
            if people:
                return people, name, notes
            notes.append(f"{name} returned nobody")
        except err_cls as exc:
            errors.append(str(exc))
            notes.append(f"{name} failed: {exc}")
    if errors and len(errors) == len(tried):
        raise RuntimeError("; ".join(errors))
    if not tried:
        raise RuntimeError("no contact provider configured: set PROSPEO_API_KEY and/or HUNTER_API_KEY in .env")
    return [], tried[-1], notes


def run(domain, company=None, fresh=False):
    """Needs step2 output. Spends provider credits only if this domain was never searched (caches are permanent)."""
    known = users.known_contacts(domain)  # named Common Room users with a found email: best leads, tier 0
    if io.exists(domain, OUTPUT_FILE) and not fresh:
        cached = io.load_json(domain, OUTPUT_FILE)
        have = {c["email"] for c in cached.get("contacts", [])}
        if all(k["email"] in have for k in known):
            return cached
    extracted = io.load_json(domain, "step2_extracted.json")
    notes, people, source = [], [], "common-room-page + prospeo"
    if not known or is_cached(domain):  # with known users we never spend credits on a search
        people, source, notes = fetch_people(domain, extracted.get("headcount"))
    contacts, rejected = pick_contacts(people, extracted.get("headcount"))
    for c in contacts:
        c["source"] = source
    if known:
        contacts = (known + [c for c in contacts if c["email"] not in {k["email"] for k in known}])[:MAX_CONTACTS]
        source = "common-room-page + prospeo" if not people else source
    result = {"domain": domain, "found": bool(contacts), "contacts": contacts, "source": source, "provider_notes": notes,
              "people_seen": len(people), "not_chosen": rejected,
              "linkedin_search": linkedin_search_url(company or extracted.get("company") or domain)}
    io.save_json(domain, OUTPUT_FILE, result)
    return result


if __name__ == "__main__":
    import sys

    print(json.dumps(run(sys.argv[1], fresh="--fresh" in sys.argv), indent=2))
