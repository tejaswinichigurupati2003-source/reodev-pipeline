"""Runs the Reo.Dev switch-campaign pipeline.

    python discover.py                          # 0. build data/accounts.csv (Common Room customers + your seeds)
    python pipeline.py                          # 1-3 for every account, then 4 (contacts) for the top-ranked ones
    python pipeline.py --domain acme.com        # one account
    python pipeline.py --max-lookups 40          # accounts to look up this run (credits are limited)
    python pipeline.py --no-contacts            # scrape, extract and score only (no Hunter credits)

Output: data/leads.csv (the outreach tracker, ranked) and data/<domain>/stepN_*.json (every decision, inspectable).
One failing account never stops the others. Nothing is ever sent: this pipeline only prepares leads.
"""
import argparse
import logging
import re
import sys

import db
import discover
import pipeline_io as io
import users
import export
from steps import s1_scrape, s2_extract, s3_score, s4_contacts, s5_draft

log = logging.getLogger("pipeline")


def research(account, fresh=False):
    """Steps 1-3 for one account. Returns the score dict, or None if the account cannot be processed."""
    domain = account["domain"]
    raw = s1_scrape.run(domain, fresh=fresh)
    if not any(p["ok"] for p in raw["pages"].values()):
        log.warning("%s: no page could be scraped", domain)
        return None
    s2_extract.run(domain, fresh=fresh)
    return s3_score.run(domain, status=account["status"])


def names_match(account):
    """Guard for guessed websites: does the company the page describes look like the account we meant?
    Only applies to domain_unconfirmed rows. Unknown extracted name = allow."""
    if account.get("status") != "domain_unconfirmed":
        return True
    company = (io.load_json(account["domain"], "step2_extracted.json").get("company") or "").lower()
    first = re.sub(r"[^a-z0-9]", "", account["name"].split("/")[0].split()[0].lower())
    return not company or not first or first in re.sub(r"[^a-z0-9]", "", company)


def run_all(accounts, max_hunter=40, contacts=True, fresh=False, tiers=("A", "B"), drafts=True):
    scored = {}
    for a in accounts:
        if not a.get("domain"):
            continue
        log.info("=== %s ===", a["domain"])
        try:
            res = research(a, fresh=fresh)
            if res:
                scored[a["domain"]] = (a, res)
        except Exception as exc:
            log.error("%s: ERROR %s", a["domain"], exc)

    if contacts:  # best accounts first, so a small Hunter budget goes where it matters
        ranked = sorted((v for v in scored.values() if v[1]["tier"] in tiers), key=lambda v: -v[1]["total"])
        spent = 0
        for a, res in ranked:
            d = a["domain"]
            if not names_match(a):
                log.info("%s: skipped contacts, page describes a different company than '%s' (check the domain)", d, a["name"])
                continue
            if not s4_contacts.is_cached(d) and not users.known_contacts(d):  # known users cost no search credit
                if spent >= max_hunter:
                    log.info("%s: skipped contacts, Hunter budget (%d) used up", d, max_hunter)
                    continue
                spent += 1
            try:
                c = s4_contacts.run(d, company=a["name"])
                if c["found"]:
                    s3_score.run(d, status=a["status"], contact_found=True)
                    if drafts:
                        s5_draft.run(d)  # drafts only: nothing in this project sends email
            except Exception as exc:
                log.error("%s: contacts ERROR %s", d, exc)
                if "usage limit" in str(exc) or "rejected the API key" in str(exc):
                    break  # quota or key problem: stop spending
        log.info("Hunter searches spent this run: %d", spent)
    path, summary = export.write_leads(accounts)
    if db.configured():  # DATABASE_URL set: publish to Neon so the dashboard shows it
        summary += f"; synced {db.sync(export.build_rows(accounts))} rows to Neon"
    return path, summary


def main(argv=None):
    p = argparse.ArgumentParser(description="Reo.Dev switch-campaign lead pipeline.")
    p.add_argument("--domain", help="run one account only")
    p.add_argument("--max-lookups", "--max-hunter", dest="max_lookups", type=int, default=40,
                   help="max accounts to look up contacts for with a NEW provider call this run (default 40)")
    p.add_argument("--no-contacts", action="store_true")
    p.add_argument("--no-drafts", action="store_true", help="skip writing email drafts (drafts are never sent)")
    p.add_argument("--fresh", action="store_true", help="redo scrape/extract/score (Hunter is never re-searched)")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    accounts = discover.load_accounts()
    if args.domain:
        dom = discover.normalize_domain(args.domain)
        accounts = [a for a in accounts if a["domain"] == dom] or [
            {"name": dom, "domain": dom, "status": "unverified", "how_identified": "manual", "source_url": "", "evidence": ""}]
    if not accounts:
        p.error("no accounts: run `python discover.py` first (or `--seed my_list.csv`)")
    path, summary = run_all(accounts, args.max_lookups, not args.no_contacts, args.fresh, drafts=not args.no_drafts)
    print(f"\nWrote {path}\n{summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
