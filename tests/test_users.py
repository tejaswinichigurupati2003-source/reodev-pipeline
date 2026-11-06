import csv

import pytest

import discover
import hunter
import llm
import pipeline_io
import prospeo
import users
from steps import s1_scrape, s2_extract, s3_score, s4_contacts

CARD = ('<a href="{href}"><p>{stat}</p><p>"{quote} and a lot more words so the quote is long enough"</p>'
        '<p>{name}</p><p>{title}</p><span>Read full story</span></a>')
HTML = ('<div class="masonryGridColumn x">'
        + CARD.format(href="/customers/atlan/", stat="30%", quote="Great tool", name="Jake Biskar", title="Pipeline Operations")
        + CARD.format(href="/customers/how-replit-used-common-room/", stat="", quote="Support got faster", name="Lena Sawyer", title="Customer Success")
        + '<a href="/customers/x/"><p>74%</p><p>more pipeline in 1 quarter</p></a>'   # a stat card with no person: ignored
        + '</div>')
ACCOUNTS = [{"name": "Atlan", "domain": "atlan.com"}, {"name": "Replit", "domain": "replit.com"}, {"name": "Pipe", "domain": "pipe.com"}]


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline_io, "DATA_DIR", tmp_path)
    for k in ("APOLLO_API_KEY", "HUNTER_API_KEY", "CONTACT_PROVIDER"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("PROSPEO_API_KEY", "k")


def test_users_parsed_stats_and_cta_ignored():
    got = discover.users_from_page(HTML, "https://www.commonroom.io/customers/")
    assert [(u["name"], u["title"]) for u in got] == [("Jake Biskar", "Pipeline Operations"), ("Lena Sawyer", "Customer Success")]
    assert got[0]["company_hint"] == "atlan"


def test_company_matched_even_when_buried_in_slug():
    assert discover.match_company("atlan", ACCOUNTS)["domain"] == "atlan.com"
    assert discover.match_company("how-replit-used-common-room-to-speed-up", ACCOUNTS)["domain"] == "replit.com"
    assert discover.match_company("something-unknown", ACCOUNTS) is None


def test_discover_users_saves_csv():
    rows = discover.discover_users(ACCOUNTS, fetch=lambda url: {"raw_html": HTML})
    discover.save_users(rows)
    saved = list(csv.DictReader(open(pipeline_io.DATA_DIR / "common_room_users.csv", encoding="utf-8")))
    assert [(r["name"], r["domain"], r["status"]) for r in saved] == [("Jake Biskar", "atlan.com", "matched"), ("Lena Sawyer", "replit.com", "matched")]


def test_lookup_cached_capped_and_stops_when_credits_out(monkeypatch):
    discover.save_users(discover.discover_users(ACCOUNTS, fetch=lambda url: {"raw_html": HTML}))
    calls = []

    def lookup(name, domain):
        calls.append(name)
        return {"email": f"{name.split()[0].lower()}@{domain}", "status": "VERIFIED"} if name.startswith("Jake") else None
    assert users.enrich_users(max_credits=5, lookup=lookup) == (2, 1)
    assert users.enrich_users(max_credits=5, lookup=lookup) == (0, 0)     # second run: everything cached
    assert calls == ["Jake Biskar", "Lena Sawyer"]
    assert [c["email"] for c in users.known_contacts("atlan.com")] == ["jake@atlan.com"]
    assert users.known_contacts("replit.com") == []                       # looked up, nothing found

    def broke(name, domain):
        raise prospeo.ProspeoError("Prospeo usage limit reached")
    discover.save_users([{"name": "New Person", "title": "t", "company": "Pipe", "domain": "pipe.com", "quote": "q", "source_url": "u", "status": "matched"}] +
                        discover.load_users())
    assert users.enrich_users(max_credits=5, lookup=broke) == (0, 0)


def test_known_user_is_first_contact_and_costs_no_search(monkeypatch):
    monkeypatch.setattr(s1_scrape.own_scraper, "fetch_doc", lambda url: {"markdown": "Atlan data catalog for teams.", "raw_html": ""} if url == "https://atlan.com" else (_ for _ in ()).throw(RuntimeError("404")))
    monkeypatch.setattr(llm, "generate_json", lambda *a, **k: {"company": "Atlan", "company_type": "data_or_ai_platform", "sells_to_developers": True, "devrel_signals": [], "missing_info": [], "reasoning": "r"})
    searched = []
    monkeypatch.setattr(prospeo, "_request", lambda p, b: (searched.append(p), (400, {"error": True, "error_code": "NO_RESULTS"}))[1])
    s1_scrape.run("atlan.com"); s2_extract.run("atlan.com"); s3_score.run("atlan.com")
    pipeline_io.save_json("atlan.com", users.CACHE, {"Jake Biskar": {"name": "Jake Biskar", "title": "Pipeline Operations", "email": "jake@atlan.com", "email_status": "VERIFIED"}})
    res = s4_contacts.run("atlan.com")
    assert searched == []                                   # no Prospeo search was made
    assert res["contacts"][0]["email"] == "jake@atlan.com" and res["contacts"][0]["persona"] == users.PERSONA
