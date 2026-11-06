"""Offline tests for Apollo, the database layer and the dashboard."""
import importlib

import pytest

import apollo
import db
import export
import hunter
import llm
import pipeline_io
from steps import s1_scrape, s2_extract, s3_score, s4_contacts

HOME = "Acme Dev builds an open-source API gateway for developers. Join our community on Discord."
LLM_REPLY = {"company": "Acme Dev", "company_type": "devtool", "sells_to_developers": True, "is_open_source": True,
             "plg_signup": True, "headcount": "50-200 employees", "devrel_signals": [], "missing_info": [], "reasoning": "r"}
HUNTER = {"data": {"emails": [
    {"value": "dana@acme.dev", "first_name": "Dana", "last_name": "Lee", "position": "Head of Developer Relations", "confidence": 90},
    {"value": "gro@acme.dev", "first_name": "Gro", "last_name": "Wth", "position": "VP Growth", "confidence": 80}]}}
APOLLO_SEARCH = {"people": [
    {"id": "1", "first_name": "Dana", "title": "Head of Developer Relations", "has_email": True, "organization": {"name": "Acme Dev"}},
    {"id": "2", "first_name": "Sam", "title": "Staff Engineer", "has_email": True, "organization": {"name": "Acme Dev"}},
    {"id": "3", "first_name": "Gro", "title": "VP Growth", "has_email": False, "organization": {"name": "Acme Dev"}},
    {"id": "4", "first_name": "Gus", "title": "Head of Growth", "has_email": True, "organization": {"name": "Acme Dev"}}]}
APOLLO_ENRICH = {"matches": [
    {"id": "1", "first_name": "Dana", "last_name": "Lee", "title": "Head of Developer Relations", "email": "dana@acme.dev", "email_status": "verified"},
    {"id": "4", "first_name": "Gus", "last_name": "Ray", "title": "Head of Growth", "email": "gus@acme.dev", "email_status": "extrapolated"}]}


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline_io, "DATA_DIR", tmp_path)
    monkeypatch.setattr(s1_scrape.own_scraper, "fetch_doc", lambda url: {"markdown": HOME, "raw_html": "<p>x</p>"} if url == "https://acme.dev" else (_ for _ in ()).throw(RuntimeError("404")))
    monkeypatch.setattr(llm, "generate_json", lambda *a, **k: dict(LLM_REPLY))
    monkeypatch.setattr(hunter, "_request", lambda d: (200, HUNTER))
    for k in ("APOLLO_API_KEY", "DATABASE_URL", "DASHBOARD_PASSWORD", "CONTACT_PROVIDER"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("HUNTER_API_KEY", "k")


@pytest.fixture
def apollo_calls(monkeypatch):
    calls = []

    def fake(method, path, params=None, json=None):
        calls.append(path)
        return 200, (APOLLO_SEARCH if "api_search" in path else APOLLO_ENRICH), {}
    monkeypatch.setattr(apollo, "_request", fake)
    return calls


def _prep():
    s1_scrape.run("acme.dev"); s2_extract.run("acme.dev"); s3_score.run("acme.dev", "verified_public")


def test_apollo_enriches_only_wanted_people_and_caches(apollo_calls):
    want = s4_contacts.make_want("50-200")
    people = apollo.find_people("acme.dev", want)
    assert [p["value"] for p in people] == ["dana@acme.dev", "gus@acme.dev"]
    assert [p["confidence"] for p in people] == [95, 60]
    apollo.find_people("acme.dev", want)
    assert apollo_calls == ["/mixed_people/api_search", "/people/bulk_match"]  # second call came from the cache


def test_apollo_blocked_falls_back_to_hunter(monkeypatch):
    monkeypatch.setenv("APOLLO_API_KEY", "k")
    monkeypatch.setattr(apollo, "_request", lambda *a, **k: (403, {}, {}))
    _prep()
    res = s4_contacts.run("acme.dev")
    assert res["source"] == "hunter" and res["found"] and "apollo failed" in res["provider_notes"][0]


def test_no_provider_configured_is_a_clear_error(monkeypatch):
    monkeypatch.delenv("HUNTER_API_KEY")
    _prep()
    with pytest.raises(RuntimeError, match="no contact provider configured"):
        s4_contacts.run("acme.dev")


def _seed_db():
    _prep()
    s4_contacts.run("acme.dev")
    acc = [{"name": "Acme Dev", "domain": "acme.dev", "status": "verified_public", "how_identified": "cr",
            "source_url": "javascript:alert(1)", "evidence": ""}]
    return db.sync(export.build_rows(acc))


def test_db_sync_keeps_tracker_notes():
    assert _seed_db() == 2
    db.update_tracker("acme.dev|dana@acme.dev", {"contact_status": "contacted", "notes": "sent li msg"}, "now")
    _seed_db()  # a re-sync must not wipe typed notes
    row = [l for l in db.list_leads() if l["email"] == "dana@acme.dev"][0]
    assert (row["contact_status"], row["notes"]) == ("contacted", "sent li msg")
    with pytest.raises(KeyError):
        db.update_tracker("nope|x", {}, "now")


def test_dashboard_requires_password_and_csrf(monkeypatch):
    monkeypatch.setenv("DASHBOARD_PASSWORD", "pw123")
    import app as appmod
    appmod = importlib.reload(appmod)
    _seed_db()
    c = appmod.app.test_client()
    assert c.get("/").status_code == 302                      # not signed in: sent to the login page
    assert c.post("/login", data={"password": "bad"}).status_code == 200
    assert c.post("/login", data={"password": "pw123"}).status_code == 302
    page = c.get("/").get_data(as_text=True)
    assert "dana@acme.dev" in page and "javascript:alert" not in page  # unsafe link never rendered
    assert c.post("/tracker", data={"key": "acme.dev|dana@acme.dev", "contact_status": "queued"}).status_code == 400
    with c.session_transaction() as s:
        token = s["csrf"]
    r = c.post("/tracker", data={"csrf": token, "key": "acme.dev|dana@acme.dev", "contact_status": "queued", "notes": "<b>x</b>"})
    assert r.status_code == 302
    assert "&lt;b&gt;x&lt;/b&gt;" in c.get("/").get_data(as_text=True)  # escaped, not rendered as HTML


def test_public_mode_needs_no_sign_in_but_keeps_csrf(monkeypatch):
    monkeypatch.setenv("DASHBOARD_PUBLIC", "1")
    import app as appmod
    appmod = importlib.reload(appmod)
    _seed_db()
    c = appmod.app.test_client()
    page = c.get("/")
    assert page.status_code == 200 and "dana@acme.dev" in page.get_data(as_text=True)
    assert "Sign out" not in page.get_data(as_text=True)
    assert c.get("/login").status_code == 302                                   # login page just goes back to the dashboard
    assert c.post("/tracker", data={"key": "acme.dev|dana@acme.dev"}).status_code == 400  # edits still need the CSRF token
    assert "noindex" in c.get("/").headers["X-Robots-Tag"]


def test_dashboard_disabled_without_password():
    import app as appmod
    appmod = importlib.reload(appmod)
    assert appmod.app.test_client().get("/").status_code == 503


def test_filters_csv_and_inline_status(monkeypatch):
    monkeypatch.setenv("DASHBOARD_PUBLIC", "1")
    import app as appmod
    appmod = importlib.reload(appmod)
    _seed_db()
    c = appmod.app.test_client()
    page = c.get("/?email=1&tier=B").get_data(as_text=True)
    assert "dana@acme.dev" in page and 'data-copy="dana@acme.dev"' in page and "leads</span>" in page
    assert "No leads match" in c.get("/?tier=C").get_data(as_text=True)
    csvtext = c.get("/export.csv?email=1").get_data(as_text=True)
    assert csvtext.splitlines()[0].lstrip("﻿").startswith("rank,tier") and "dana@acme.dev" in csvtext
    with c.session_transaction() as s:
        s["csrf"] = "tok"
    c.post("/tracker", data={"csrf": "tok", "key": "acme.dev|dana@acme.dev", "contact_status": "replied"})
    assert "No leads match" in c.get("/?status=queued").get_data(as_text=True)
    assert "dana@acme.dev" in c.get("/?status=replied").get_data(as_text=True)


def test_board_drag_status_and_manual_contacts(monkeypatch):
    monkeypatch.setenv("DASHBOARD_PUBLIC", "1")
    import app as appmod
    appmod = importlib.reload(appmod)
    _seed_db()
    c = appmod.app.test_client()
    page = c.get("/board").get_data(as_text=True)
    assert "Outreach board" in page and 'data-key="acme.dev|dana@acme.dev"' in page and "+ Add contact" in page
    with c.session_transaction() as s:
        s["csrf"] = "tok"
    ok = lambda **kw: c.post("/tracker/status", json=kw, headers={"X-CSRF": "tok"}).status_code
    assert c.post("/tracker/status", json={"key": "acme.dev|dana@acme.dev", "status": "replied"}).status_code == 400   # no CSRF
    db.update_tracker("acme.dev|dana@acme.dev", {"contact_status": "contacted", "notes": "keep me"}, "now")
    assert ok(key="acme.dev|dana@acme.dev", status="replied") == 200
    assert ok(key="acme.dev|dana@acme.dev", status="bogus") == 400 and ok(key="nope|x", status="replied") == 404
    row = [l for l in db.list_leads() if l["email"] == "dana@acme.dev"][0]
    assert (row["contact_status"], row["notes"]) == ("replied", "keep me")      # drag changes only the stage
    # manual contacts
    assert c.post("/contacts/add", data={"company": "Notion", "email": "a@notion.so"}).status_code == 400            # CSRF
    r = c.post("/contacts/add", data={"csrf": "tok", "company": "Notion", "name": "Erica A", "email": "erica@notion.so", "status": "queued"})
    assert r.status_code == 302
    assert c.post("/contacts/add", data={"csrf": "tok", "company": "", "email": "x"}).status_code == 400
    assert c.post("/contacts/add", data={"csrf": "tok", "company": "Bad", "email": "not-an-email"}).status_code == 400
    mine = [l for l in db.list_leads() if l.get("manual")]
    assert len(mine) == 1 and mine[0]["contact_status"] == "queued" and mine[0]["tier"] == "MY"
    assert ok(key=mine[0]["key"], status="contacted") == 200
    _seed_db()                                                                         # a re-sync keeps manual contacts
    assert len([l for l in db.list_leads() if l.get("manual")]) == 1
    assert "Notion" in c.get("/board?tier=MY").get_data(as_text=True)
    assert c.post("/contacts/delete", data={"csrf": "tok", "key": "acme.dev|dana@acme.dev"}).status_code == 404   # synced leads cannot be deleted
    assert c.post("/contacts/delete", data={"csrf": "tok", "key": mine[0]["key"]}).status_code == 302
    assert not [l for l in db.list_leads() if l.get("manual")]
