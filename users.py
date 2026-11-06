"""Find the emails of the named Common Room users (people quoted on commonroom.io/customers).

    python discover.py --users     # 1. extract the people -> data/common_room_users.csv
    python users.py                # 2. look up their emails with Prospeo (max 30 credits by default)

Each lookup costs at most 1 Prospeo credit (nothing when no email is found) and is cached per domain in
data/<domain>/known_users.json, so a person is never looked up twice. `--max-credits N` caps spend.
"""
import argparse

import discover
import pipeline_io as io
import prospeo

CACHE = "known_users.json"
PERSONA = "Common Room user (named on their customers page)"


def load_known(domain):
    return io.load_json(domain, CACHE) if io.exists(domain, CACHE) else {}


def enrich_users(max_credits=30, lookup=None):
    """Look up emails for matched users. Returns (looked_up, found). `lookup(name, domain)` is replaceable for tests."""
    lookup = lookup or prospeo.enrich_known
    spent = found = 0
    for u in discover.load_users():
        d = u.get("domain")
        if not d:
            continue
        cache = load_known(d)
        if u["name"] in cache:
            continue
        if spent >= max_credits:
            break
        try:
            res = lookup(u["name"], d)
        except prospeo.ProspeoError as exc:
            print(f"stopped: {exc}")
            if "usage limit" in str(exc) or "rejected the API key" in str(exc):
                break
            continue
        spent += 1
        cache[u["name"]] = {"name": u["name"], "title": u["title"], "source_url": u["source_url"],
                            "email": (res or {}).get("email"), "email_status": (res or {}).get("status"),
                            "linkedin": (res or {}).get("linkedin_url")}
        found += bool(res)
        io.save_json(d, CACHE, cache)
    return spent, found


def known_contacts(domain):
    """Known users with an email, shaped like step 4's contacts (tier 0 = best)."""
    return [{"email": k["email"], "name": k["name"], "title": k["title"], "persona": PERSONA, "tier": 0,
             "hunter_confidence": 95 if (k.get("email_status") or "").upper() == "VERIFIED" else 60,
             "linkedin": k.get("linkedin"), "source": "common-room-page + prospeo"}
            for k in load_known(domain).values() if k.get("email")]


def main():
    p = argparse.ArgumentParser(description="Find emails of named Common Room users.")
    p.add_argument("--max-credits", type=int, default=30)
    args = p.parse_args()
    spent, found = enrich_users(args.max_credits)
    print(f"looked up {spent} people, found {found} emails (credits used: up to {found})")


if __name__ == "__main__":
    main()
