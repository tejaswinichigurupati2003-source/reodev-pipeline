"""Step 0 - Build the account list: companies that appear to be Common Room customers.

    python discover.py                      # scrape commonroom.io customer pages -> data/accounts.csv
    python discover.py --seed my_list.csv   # add accounts you found by hand (columns: name,domain,source_url,how_identified)

Every account keeps HOW it was identified (`how_identified`, `source_url`, `evidence`) and a
`status` of `verified_public` (Common Room's own site names it) or `unverified` (everything else:
manual seeds, G2 lists, LinkedIn posts ...). We never guess a domain: if a customer name cannot be
resolved to a website from the page itself, `domain` stays empty and the row is flagged
`needs_domain` so a person fills it in.
"""
import argparse
import csv
import re
from pathlib import Path
from urllib.parse import urlparse

from bs4 import BeautifulSoup

import pipeline_io as io
from scrapers import own_scraper

START_PAGES = ["https://www.commonroom.io/customers/", "https://www.commonroom.io/case-studies/"]
FIELDS = ["name", "domain", "status", "how_identified", "source_url", "evidence"]
# Hosts that are never "the customer's own website".
IGNORE_HOSTS = ("commonroom.io", "linkedin.com", "twitter.com", "x.com", "facebook.com", "youtube.com", "instagram.com",
                "github.com", "g2.com", "google.com", "apple.com", "calendly.com", "hubspot.com", "zoom.us", "zoom.com",
                "medium.com", "notion.so", "wistia.com", "vimeo.com", "cloudfront.net", "gstatic.com")


def normalize_domain(value):
    """'https://www.Acme.com/path' -> 'acme.com'. Empty string if it is not a usable domain."""
    value = (value or "").strip().lower()
    if not value:
        return ""
    host = urlparse(value if "//" in value else "//" + value).hostname or ""
    host = re.sub(r"^www\.", "", host)
    return host if "." in host else ""


def clean_name(name):
    """Customer name from messy page text, or None if it is not a company name (stats, video captions ...)."""
    name = (name or "").strip()
    m = re.search(r"Common Room x ([^\"]+)", name)
    if m:
        name = m.group(1).strip()
    name = re.sub(r"\s*(logo|case study)\s*$", "", name, flags=re.I).strip()
    if not (1 < len(name) <= 40) or re.search(r"\d|%|(more|faster|booked|pipeline|meetings|title card)", name, re.I):
        return None
    return name


def _ignored(host):
    return any(host == h or host.endswith("." + h) for h in IGNORE_HOSTS)


def customers_from_page(html, page_url):
    """Customer names from one Common Room page: logo alt text and case-study links.
    Returns [{"name", "case_study_url", "evidence"}]. Pure function: no network."""
    soup = BeautifulSoup(html, "lxml")
    found = {}
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if re.search(r"/(customers|case-studies?)/[\w-]+/?$", href):
            img = a.find("img")
            name = (img.get("alt") if img else None) or a.get_text(" ", strip=True)
            name = clean_name(name)
            if name:
                url = href if href.startswith("http") else page_url.rstrip("/").rsplit("/", 1)[0] + "/" + href.lstrip("/")
                found.setdefault(name.lower(), {"name": name, "case_study_url": url,
                                                "evidence": f"linked from {page_url} as '{name}'"})
    for img in soup.find_all("img", alt=True):  # logo walls with no link
        name = clean_name(img["alt"])
        if name and name.lower() not in found and "common room" not in name.lower():
            found[name.lower()] = {"name": name, "case_study_url": None, "evidence": f"logo on {page_url} (alt text '{img['alt']}')"}
    return list(found.values())


def domain_from_case_study(html, name):
    """The customer's own site, read from a case study page: the outbound link whose host looks like the name."""
    soup = BeautifulSoup(html, "lxml")
    slug = re.sub(r"[^a-z0-9]", "", name.lower())
    for a in soup.find_all("a", href=True):
        host = normalize_domain(a["href"]) if a["href"].startswith("http") else ""
        if host and not _ignored(host) and slug and slug[:6] in re.sub(r"[^a-z0-9]", "", host):
            return host  # the host contains the company name: confident
    return None  # a random outbound link is NOT good enough: leave blank for a human


def discover(fetch=None):
    """Scrape Common Room's public customer pages. `fetch(url)` -> own_scraper.fetch_doc-like dict (tests replace it)."""
    fetch = fetch or own_scraper.fetch_doc
    rows, seen = [], set()
    for start in START_PAGES:
        try:
            doc = fetch(start)
        except Exception as exc:
            print(f"  could not read {start}: {exc}")
            continue
        for c in customers_from_page(doc["raw_html"], start):
            if c["name"].lower() in seen:
                continue
            seen.add(c["name"].lower())
            domain = ""
            if c["case_study_url"]:
                try:
                    domain = domain_from_case_study(fetch(c["case_study_url"])["raw_html"], c["name"]) or ""
                except Exception:
                    pass
            rows.append({"name": c["name"], "domain": domain, "status": "verified_public" if domain else "needs_domain",
                         "how_identified": "Named as a customer on Common Room's public site",
                         "source_url": c["case_study_url"] or start, "evidence": c["evidence"]})
    return rows


def read_seed(path):
    rows = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            domain = normalize_domain(r.get("domain"))
            rows.append({"name": (r.get("name") or domain).strip(), "domain": domain,
                         "status": "unverified" if domain else "needs_domain",
                         "how_identified": (r.get("how_identified") or "manual seed").strip(),
                         "source_url": (r.get("source_url") or "").strip(), "evidence": (r.get("evidence") or "").strip()})
    return rows


def merge(existing, new):
    """Keep one row per domain (or per name when the domain is unknown). Verified beats unverified."""
    rank = {"verified_public": 0, "unverified": 1, "needs_domain": 2}
    out = {}
    for r in existing + new:
        key = r["domain"] or "name:" + r["name"].lower()
        if key not in out or rank.get(r["status"], 3) < rank.get(out[key]["status"], 3):
            out[key] = r
    return list(out.values())


def load_accounts():
    path = io.DATA_DIR / "accounts.csv"
    if not path.exists():
        return []
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def save_accounts(rows):
    path = io.DATA_DIR / "accounts.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    return path


def main(argv=None):
    p = argparse.ArgumentParser(description="Build the Common Room customer account list.")
    p.add_argument("--seed", help="CSV of accounts you found by hand")
    p.add_argument("--no-scrape", action="store_true", help="only merge --seed, skip commonroom.io")
    args = p.parse_args(argv)
    new = [] if args.no_scrape else discover()
    if args.seed:
        new += read_seed(args.seed)
    rows = merge(load_accounts(), new)
    path = save_accounts(rows)
    by = {}
    for r in rows:
        by[r["status"]] = by.get(r["status"], 0) + 1
    print(f"{len(rows)} accounts saved to {path}: {by}")
    if by.get("needs_domain"):
        print("Rows marked needs_domain have no website yet: fill the `domain` column by hand, then run the pipeline.")


if __name__ == "__main__":
    main()
