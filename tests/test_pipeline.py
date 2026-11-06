"""Offline tests: every network call (site fetch, LLM, Hunter) is replaced with canned data."""
import csv

import pytest

import discover
import hunter
import llm
import pipeline
import pipeline_io
from steps import s1_scrape, s2_extract, s3_score, s4_contacts

HOME = "Acme Dev builds an open-source API gateway for developers. Join our community on Discord. Get started free."
HTML = ('<html><body><a href="https://github.com/acmedev">gh</a>'
        '<a href="https://discord.gg/acme">chat</a><p>%s</p></body></html>' % HOME)

LLM_REPLY = {
    "company": "Acme Dev", "product_summary": "API gateway", "company_type": "devtool", "sells_to_developers": True,
    "is_open_source": True, "plg_signup": True, "headcount": "50-200 employees", "use_case_for_intent": "x",
    "devrel_signals": [
        {"signal": "community on Discord", "evidence_quote": "Join our community on Discord", "source_url": "https://acme.dev"},
        {"signal": "invented", "evidence_quote": "We are hiring a DevRel lead", "source_url": "https://acme.dev"},
    ],
    "missing_info": [], "reasoning": "r",
}
HUNTER = {"data": {"emails": [
    {"value": "ceo@acme.dev", "first_name": "Cee", "last_name": "Oh", "position": "CEO", "confidence": 95},
    {"value": "dana@acme.dev", "first_name": "Dana", "last_name": "Lee", "position": "Head of Developer Relations", "confidence": 90},
    {"value": "low@acme.dev", "first_name": "Lo", "last_name": "Conf", "position": "Community Manager", "confidence": 40},
    {"value": "eng@acme.dev", "first_name": "En", "last_name": "Gi", "position": "Staff Engineer", "confidence": 99},
    {"value": "gro@acme.dev", "first_name": "Gro", "last_name": "Wth", "position": "VP Growth", "confidence": 80},
]}}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline_io, "DATA_DIR", tmp_path)
    monkeypatch.setenv("HUNTER_API_KEY", "k")
    for k in ("APOLLO_API_KEY", "DATABASE_URL", "CONTACT_PROVIDER"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(s1_scrape.own_scraper, "fetch_doc", lambda url: {"markdown": HOME, "raw_html": HTML} if url == "https://acme.dev" else (_ for _ in ()).throw(RuntimeError("404")))
    monkeypatch.setattr(llm, "generate_json", lambda *a, **k: dict(LLM_REPLY))
    calls = []
    monkeypatch.setattr(hunter, "_request", lambda d: (calls.append(d), (200, HUNTER))[1])
    return calls


def test_fabricated_quote_is_dropped_and_signals_found():
    s1_scrape.run("acme.dev")
    ex = s2_extract.run("acme.dev")
    assert [s["signal"] for s in ex["devrel_signals"]] == ["community on Discord"]
    assert ex["dropped_unverified_quotes"] == 1
    assert ex["github_org"] == "https://github.com/acmedev"
    assert ex["community_links"] == ["https://discord.gg/acme"]


def test_score_and_tier():
    s1_scrape.run("acme.dev"); s2_extract.run("acme.dev")
    sc = s3_score.run("acme.dev", status="verified_public")
    assert (sc["fit"], sc["value"], sc["switching"], sc["ease"]) == (40, 12, 20, 5)


def test_total_is_tier_a():
    s1_scrape.run("acme.dev"); s2_extract.run("acme.dev")
    sc = s3_score.run("acme.dev", status="verified_public")
    assert sc["total"] == 77 and sc["tier"] == "A"


def test_non_developer_company_is_skipped():
    out = s3_score.score_account({"sells_to_developers": False, "company_type": "non_software", "devrel_signals": []})
    assert out["tier"] == "SKIP"


def test_contacts_ranked_and_filtered():
    contacts, rejected = s4_contacts.pick_contacts(HUNTER["data"]["emails"], "50-200")
    assert [c["email"] for c in contacts] == ["dana@acme.dev", "gro@acme.dev"]  # CEO excluded at 50+, low-confidence + engineer excluded
    why = {r["email"]: r["why"] for r in rejected}
    assert "founder/CEO" in why["ceo@acme.dev"] and "confidence" in why["low@acme.dev"] and "persona" in why["eng@acme.dev"]


def test_small_company_allows_ceo():
    contacts, _ = s4_contacts.pick_contacts(HUNTER["data"]["emails"], "10 employees")
    assert [c["email"] for c in contacts] == ["dana@acme.dev", "gro@acme.dev", "ceo@acme.dev"]


def test_hunter_never_searched_twice(isolated):
    s1_scrape.run("acme.dev"); s2_extract.run("acme.dev")
    s4_contacts.run("acme.dev"); s4_contacts.run("acme.dev", fresh=True)
    assert isolated == ["acme.dev"]


def test_end_to_end_csv_and_budget(isolated, tmp_path):
    accounts = [{"name": "Acme Dev", "domain": "acme.dev", "status": "verified_public", "how_identified": "cr site", "source_url": "u", "evidence": "e"},
                {"name": "NoSite", "domain": "", "status": "needs_domain", "how_identified": "x", "source_url": "", "evidence": ""}]
    path, summary = pipeline.run_all(accounts, max_hunter=5)
    rows = list(csv.DictReader(open(path, encoding="utf-8-sig")))
    assert [r["email"] for r in rows] == ["dana@acme.dev", "gro@acme.dev"]
    assert rows[0]["tier"] == "A" and rows[0]["ease"] == "15"  # verified (5) + contact found (10)
    assert "1 accounts scored" in summary


def test_budget_zero_spends_nothing(isolated):
    accounts = [{"name": "Acme Dev", "domain": "acme.dev", "status": "unverified", "how_identified": "m", "source_url": "", "evidence": ""}]
    pipeline.run_all(accounts, max_hunter=0)
    assert isolated == []


def test_discover_names_and_no_guessed_domains():
    page = ('<a href="/customers/notion"><img alt="Notion logo"></a><a href="/customers/acme"><img alt="Acme Dev logo"></a>'
            '<img alt="Loose Co logo">')
    names = {c["name"] for c in discover.customers_from_page(page, "https://www.commonroom.io/customers/")}
    assert names == {"Notion", "Acme Dev", "Loose Co"}
    study = '<a href="https://www.linkedin.com/x">li</a><a href="https://acme.dev/">site</a><a href="https://random.org">r</a>'
    assert discover.domain_from_case_study(study, "Acme Dev") == "acme.dev"
    assert discover.domain_from_case_study('<a href="https://random.org">r</a>', "Acme Dev") is None


def test_merge_prefers_verified():
    a = {"name": "A", "domain": "a.io", "status": "unverified"}
    b = {"name": "A", "domain": "a.io", "status": "verified_public"}
    assert discover.merge([a], [b])[0]["status"] == "verified_public"


def test_csv_formula_injection_neutralised():
    import export
    assert export._safe("=HYPERLINK(1)").startswith("'")


def test_clean_name_rejects_stats_and_unwraps_captions():
    assert discover.clean_name("30% more meetings per rep") is None
    assert discover.clean_name('Video title card with title: "Common Room x Semgrep"') == "Semgrep"
    assert discover.clean_name("Notion logo") == "Notion"
