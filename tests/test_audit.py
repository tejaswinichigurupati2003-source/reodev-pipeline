"""Tests for the full-app audit: every feature the brief lists, against a real (SQLite) database, through the real Flask app."""
import importlib
import re
from urllib.parse import quote

import pytest

import db
import pipeline_io


def lead(i, **kw):
    r = {c: None for c in db.LEAD_COLUMNS}
    r.update({"rank": i, "tier": "A" if i % 3 == 0 else "B", "score": 100 - i, "company": f"Company {i:02d}", "domain": f"c{i}.dev",
             "account_status": "verified_public", "how_identified": "test", "company_type": "devtool", "fit": 1, "value": 1,
             "switching_proxy": 1, "ease": 1, "contact_name": f"Person {i}", "contact_title": "Head of Growth" if i % 2 else "RevOps Lead",
             "email": f"p{i}@c{i}.dev", "email_confidence": 90, "contact_source": "prospeo", "persona": "Growth / Demand Gen / Marketing leader"})
    r.update(kw)
    return r


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline_io, "DATA_DIR", tmp_path)
    for k in ("DATABASE_URL", "DASHBOARD_PASSWORD", "RESPONSES_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("DASHBOARD_PUBLIC", "1")
    db.sync([lead(i) for i in range(1, 31)] + [lead(31, email=None, contact_name=None, contact_title=None)])
    import app as appmod
    appmod = importlib.reload(appmod)
    c = appmod.app.test_client()
    c.get("/board")
    with c.session_transaction() as s:
        s["csrf"] = "tok"
    c.tok = "tok"
    return c


def key(i):
    return f"c{i}.dev|p{i}@c{i}.dev"


def post(c, url, **data):
    return c.post(url, data={"csrf": c.tok, **data})


# ---------------------------------------------------------------- navigation and pages
def test_every_page_opens_and_unknown_pages_get_a_real_error_page(client):
    for url in ("/", "/board", "/list", "/interview", f"/lead?key={quote(key(1))}", f"/interview/guide?key={quote(key(1))}", "/research"):
        assert client.get(url, follow_redirects=True).status_code == 200, url
    r = client.get("/lead?key=nope")
    html = r.get_data(as_text=True)
    assert r.status_code == 404 and "Back to the board" in html and "could not find that lead" in html     # styled, with a way out
    assert client.get("/no-such-page").status_code == 404
    assert client.get("/favicon.ico").status_code == 204


def test_board_cards_and_list_rows_link_to_the_profile(client):
    board = client.get("/board").get_data(as_text=True)
    assert 'href="/lead?key=c1.dev%7Cp1' in board
    lst = client.get("/list").get_data(as_text=True)
    assert "/lead?key=" in lst and "Open, edit, notes" in lst


# ---------------------------------------------------------------- profile editing
def test_edit_contact_details_survive_a_sync_and_validate(client):
    k = key(2)
    r = post(client, "/lead/edit", key=k, contact_name="New Name", contact_title="CMO", email="new@c2.dev", linkedin="https://linkedin.com/in/x")
    assert r.status_code == 302
    page = client.get(f"/lead?key={quote(k)}").get_data(as_text=True)
    assert "New Name" in page and "new@c2.dev" in page and "Contact details saved" in page      # message shown on the page we land on
    db.sync([lead(i) for i in range(1, 31)])                                                         # the pipeline re-syncs: edit must stay
    l = db.get_lead(k)
    assert (l["contact_name"], l["email"], l["edited"]) == ("New Name", "new@c2.dev", True)
    # validation: nothing is saved, the reason is shown
    for bad, msg in [({"email": "nope"}, "email address"), ({"email": "p3@c3.dev"}, "already uses"), ({"linkedin": "javascript:alert(1)"}, "http")]:
        post(client, "/lead/edit", key=k, contact_name="Should Not Save", **bad)
        assert db.get_lead(k)["contact_name"] == "New Name"
    # clearing a field is allowed and respected
    post(client, "/lead/edit", key=k, contact_name="New Name", contact_title="", email="", linkedin="")
    assert db.get_lead(k)["email"] is None
    assert post(client, "/lead/edit", key="nope|x", contact_name="x").status_code == 404


def test_flash_message_is_shown_once_after_saving(client):
    k = key(4)
    r = post(client, "/lead/edit", key=k, contact_name="Flash Test")
    page = client.get(r.headers["Location"]).get_data(as_text=True)
    assert "Contact details saved." in page
    assert "Contact details saved." not in client.get(r.headers["Location"]).get_data(as_text=True)


def test_notes_and_stage_save_and_persist(client):
    k = key(5)
    post(client, "/tracker", key=k, contact_status="contacted", notes="called <b>Sam</b>", channel="email", next=f"/lead?key={quote(k)}")
    page = client.get(f"/lead?key={quote(k)}").get_data(as_text=True)
    assert "called &lt;b&gt;Sam&lt;/b&gt;" in page and "Saved." in page
    l = db.get_lead(k)
    assert (l["contact_status"], l["notes"], l["channel"]) == ("contacted", "called <b>Sam</b>", "email")
    # an unknown stage is refused and reported, never silently turned into something else
    post(client, "/tracker", key=k, contact_status="bogus", notes="x", next=f"/lead?key={quote(k)}")
    assert db.get_lead(k)["contact_status"] == "contacted" and db.get_lead(k)["notes"] == "called <b>Sam</b>"


def test_stage_endpoint_changes_only_the_stage_and_reports_errors_as_json(client):
    k = key(6)
    post(client, "/tracker", key=k, contact_status="queued", notes="keep this note")
    ok = client.post("/tracker/status", json={"key": k, "status": "replied"}, headers={"X-CSRF": "tok"})
    assert ok.get_json() == {"ok": True}
    l = db.get_lead(k)
    assert (l["contact_status"], l["notes"]) == ("replied", "keep this note")
    for payload, code in [({"key": k, "status": "bogus"}, 400), ({"key": "nope|x", "status": "replied"}, 404)]:
        r = client.post("/tracker/status", json=payload, headers={"X-CSRF": "tok"})
        assert r.status_code == code and r.get_json()["ok"] is False and r.get_json()["error"]
    assert client.post("/tracker/status", json={"key": k, "status": "queued"}).status_code == 400   # no CSRF token


def test_redirect_after_form_never_leaves_the_site(client):
    k = key(7)
    r = client.post("/tracker", data={"csrf": "tok", "key": k, "contact_status": "queued", "next": "https://evil.example/x"})
    assert r.status_code == 302 and "evil.example" not in r.headers["Location"]
    r = client.post("/tracker", data={"csrf": "tok", "key": k, "contact_status": "queued", "next": "//evil.example/x"},
                    headers={"Referer": "https://evil.example/page"})
    assert "evil.example" not in r.headers["Location"]


# ---------------------------------------------------------------- archive and manual contacts
def test_archive_hides_everywhere_and_restore_brings_it_back(client):
    k = key(8)
    assert post(client, "/lead/archive", key=k, archive="1").status_code == 302
    assert "Company 08" not in client.get("/board").get_data(as_text=True)
    assert "Company 08" not in client.get("/list").get_data(as_text=True)
    assert "Company 08" in client.get("/list?archived=1").get_data(as_text=True)
    assert db.get_lead(k)["archived"] is True                      # nothing deleted
    post(client, "/lead/archive", key=k, archive="")
    assert "Company 08" in client.get("/board").get_data(as_text=True)


def test_add_edit_delete_manual_contact_and_duplicate_protection(client):
    fetch = {"X-Requested-With": "fetch"}
    r = client.post("/contacts/add", data={"csrf": "tok", "company": "Notion", "name": "Erica", "email": "erica@notion.so", "status": "queued"}, headers=fetch)
    assert r.get_json()["ok"] is True
    k = r.get_json()["key"]
    dup = client.post("/contacts/add", data={"csrf": "tok", "company": "Notion 2", "email": "ERICA@notion.so"}, headers=fetch)
    assert dup.status_code == 400 and "already on the board" in dup.get_json()["error"]
    dup2 = client.post("/contacts/add", data={"csrf": "tok", "company": "X", "email": "p1@c1.dev"}, headers=fetch)       # same as a synced lead
    assert dup2.status_code == 400
    post(client, "/lead/edit", key=k, company="Notion Labs", domain="notion.so", contact_name="Erica A", contact_title="CRO", email="erica@notion.so", linkedin="")
    l = db.get_lead(k)
    assert (l["company"], l["contact_title"], l["contact_status"]) == ("Notion Labs", "CRO", "queued")
    assert post(client, "/lead/edit", key=k, company="", email="").status_code == 302 and db.get_lead(k)["company"] == "Notion Labs"
    assert post(client, "/contacts/delete", key=k).status_code == 302 and db.get_lead(k) is None
    assert post(client, "/contacts/delete", key=key(1)).status_code == 404       # synced leads can only be archived


# ---------------------------------------------------------------- search, filters, sorting, paging
def test_search_matches_company_person_title_and_email_and_clears(client):
    names = lambda url: set(re.findall(r"Company \d\d", client.get(url).get_data(as_text=True)))
    assert names("/list?q=p9@c9.dev") == {"Company 09"}                     # email
    assert "Company 12" in names("/list?q=person+12")                       # person
    assert len(names("/list?q=revops")) > 5                                 # title
    assert names("/board?q=company+05") == {"Company 05"}                   # the board searches too
    assert "No leads match" in client.get("/list?q=zzzzz").get_data(as_text=True)
    assert len(names("/list")) == 25                                        # cleared: the first page again


def test_filters_combine_and_sorting_orders(client):
    post(client, "/tracker", key=key(3), contact_status="contacted")
    post(client, "/tracker", key=key(6), contact_status="contacted")
    both = set(re.findall(r"Company \d\d", client.get("/list?tier=A&status=contacted&email=1").get_data(as_text=True)))
    assert both == {"Company 03", "Company 06"}
    assert "Company 09" not in both
    order = lambda sort: [int(n) for n in re.findall(r"Company (\d\d)", client.get(f"/list?sort={sort}&q=person+1").get_data(as_text=True))]
    assert order("company") == sorted(order("company"))
    assert order("score") == sorted(order("score"))                         # score falls as the number rises, so ascending numbers
    page = client.get("/list?sort=bogus").get_data(as_text=True)
    assert re.search(r"<option value=\"interview\" selected>", page)         # an invalid sort falls back to the default safely


def test_pagination_pages_and_bounds(client):
    p1 = set(re.findall(r"Company \d\d", client.get("/list").get_data(as_text=True)))
    p2 = set(re.findall(r"Company \d\d", client.get("/list?page=2").get_data(as_text=True)))
    assert len(p1) == 25 and len(p2) == 6 and not (p1 & p2)                  # 31 leads, no duplicates, none missing
    assert p1 | p2 == {f"Company {i:02d}" for i in range(1, 32)}
    assert "Page 2 of 2" in client.get("/list?page=2").get_data(as_text=True)
    assert client.get("/list?page=999").status_code == 200 and client.get("/list?page=abc").status_code == 200
    assert "page=2" in client.get("/list?sort=score").get_data(as_text=True)      # paging keeps the other filters


# ---------------------------------------------------------------- dashboard numbers and charts
def test_metric_cards_and_funnel_link_to_matching_filtered_views(client):
    for i in (1, 2, 3):
        post(client, "/tracker", key=key(i), contact_status="contacted")
    post(client, "/tracker", key=key(4), contact_status="replied")
    page = client.get("/list").get_data(as_text=True)
    assert re.search(r'href="/list\?status=contacted"[^>]*><b>3</b>', page)
    assert re.search(r'href="/list\?status=replied"[^>]*><b>1</b>', page)
    assert re.search(r'href="/list\?email=1"[^>]*><b>30</b>', page)          # 30 of 31 leads have an email
    assert "Pipeline by stage" in page and 'href="/list?status=contacted" title="Show 3 leads in contacted"' in page
    clicked = set(re.findall(r"Company \d\d", client.get("/list?status=contacted").get_data(as_text=True)))
    assert clicked == {"Company 01", "Company 02", "Company 03"}              # the card's number matches what it opens


def test_board_columns_and_counts_match_the_data(client):
    post(client, "/tracker", key=key(1), contact_status="queued")
    html = client.get("/board").get_data(as_text=True)
    col = lambda s: re.search(rf'data-status="{s}".*?class="count">(\d+)<', html, re.S).group(1)
    assert col("queued") == "1" and col("not contacted") == "30"
    assert 'Nothing here yet. Drag a card in.' in html


def test_csv_export_respects_filters_and_neutralises_formulas(client):
    post(client, "/lead/edit", key=key(9), contact_name="=HYPERLINK(1)", email="")
    text = client.get("/export.csv?q=company+09").get_data(as_text=True)
    assert "'=HYPERLINK(1)" in text and "Company 10" not in text
    assert text.splitlines()[0].lstrip("﻿").startswith("rank,tier")


# ---------------------------------------------------------------- robustness
def test_pages_survive_a_database_error_and_json_endpoints_answer_json(client, monkeypatch):
    import app as appmod
    monkeypatch.setattr(appmod.db, "set_status", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db down")))
    r = client.post("/tracker/status", json={"key": key(1), "status": "queued"}, headers={"X-CSRF": "tok"})
    assert r.status_code == 500 and r.get_json()["ok"] is False                  # a clean JSON error, never an HTML crash page
    monkeypatch.setattr(appmod.db, "all_leads", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db down")))
    page = client.get("/board")
    assert page.status_code == 500 and "Something went wrong" in page.get_data(as_text=True)


def test_password_mode_still_protects_the_dashboard_but_not_the_form(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline_io, "DATA_DIR", tmp_path)
    monkeypatch.delenv("DASHBOARD_PUBLIC", raising=False)
    monkeypatch.setenv("DASHBOARD_PASSWORD", "pw")
    import app as appmod
    c = importlib.reload(appmod).app.test_client()
    for url in ("/board", "/list", f"/lead?key={quote(key(1))}", "/interview", "/export.csv"):
        assert c.get(url).status_code == 302, url
    assert c.post("/tracker/status", json={"key": "x", "status": "queued"}).status_code == 302
    assert c.get("/research").status_code == 200 and c.get("/favicon.ico").status_code == 204
