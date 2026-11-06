"""Performance and concurrency tests. Realistic volume (5,000 leads), measured through the real Flask app.
Thresholds are generous so a slow CI machine does not flake, but tight enough to catch an accidental return to
"load everything, render everything, query on every request"."""
import importlib
import time
from urllib.parse import quote

import pytest

import db
import pipeline_io

N = 5000


def make(i):
    r = {c: None for c in db.LEAD_COLUMNS}
    r.update({"rank": i, "tier": "ABC"[i % 3], "score": 100 - (i % 90), "company": f"Company {i:05d}", "domain": f"c{i}.dev",
              "account_status": "verified_public", "how_identified": "test " * 20, "company_type": "devtool", "fit": 1, "value": 1,
              "switching_proxy": 1, "ease": 1, "contact_name": f"Person {i}", "contact_title": "Head of Growth",
              "email": f"p{i}@c{i}.dev", "email_confidence": 90, "contact_source": "prospeo", "persona": "Growth / Demand Gen / Marketing leader",
              "devrel_evidence": "community signal " * 30, "draft_subject": "Subject", "draft_body": "Hello " * 120})
    return r


@pytest.fixture(scope="module")
def big(tmp_path_factory):
    path = tmp_path_factory.mktemp("perf")
    old = pipeline_io.DATA_DIR
    pipeline_io.DATA_DIR = path
    import os
    for k in ("DATABASE_URL", "DASHBOARD_PASSWORD"):
        os.environ.pop(k, None)
    os.environ["DASHBOARD_PUBLIC"] = "1"
    db.sync([make(i) for i in range(1, N + 1)])
    import app as appmod
    appmod = importlib.reload(appmod)
    c = appmod.app.test_client()
    c.get("/board")
    with c.session_transaction() as s:
        s["csrf"] = "tok"
    yield c
    pipeline_io.DATA_DIR = old
    os.environ.pop("DASHBOARD_PUBLIC", None)


def timed(c, url, **kw):
    t = time.perf_counter()
    r = c.get(url, **kw)
    return r, (time.perf_counter() - t) * 1000


def qcount(r):
    import re
    return int(re.search(r'desc="(\d+) queries"', r.headers["Server-Timing"]).group(1))


def test_list_page_is_small_and_fast_with_5000_leads(big):
    r, ms = timed(big, "/list")
    assert r.status_code == 200 and ms < 2500, ms
    assert len(r.data) < 140_000, len(r.data)          # 25 rows, not 5,000 (it was 306 KB for 71 rows before)
    assert r.get_data(as_text=True).count('class="row"') == 25
    assert "Page 1 of 200" in r.get_data(as_text=True)
    r, ms = timed(big, "/list?page=200")
    assert r.get_data(as_text=True).count('class="row"') == 25 and ms < 2500


def test_board_draws_a_capped_number_of_cards_but_counts_everything(big):
    r, ms = timed(big, "/board")
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and ms < 2500, ms
    assert html.count('class="card"') == 30                           # one column holds all 5,000: only 30 are drawn
    assert f'data-total="{N}"' in html and f'class="count">{N}<' in html     # the header still tells the truth
    assert f"Show all {N} in the list view" in html
    assert len(r.data) < 120_000, len(r.data)


def test_search_is_fast_and_exact_at_volume(big):
    r, ms = timed(big, "/list?q=c4321.dev")
    html = r.get_data(as_text=True)
    assert ms < 2500 and html.count('class="row"') == 1 and "Company 04321" in html
    r, ms = timed(big, "/list?q=zzzz-nothing")
    assert "No leads match" in r.get_data(as_text=True) and ms < 2500


def test_partial_responses_carry_only_the_results(big):
    full, _ = timed(big, "/list?q=person+12")
    part, _ = timed(big, "/list?q=person+12", headers={"X-Requested-With": "fetch"})
    assert b"<html" not in part.data and b'class="side"' not in part.data and b'class="row"' in part.data
    assert len(part.data) < len(full.data)
    b, _ = timed(big, "/board?q=company+00007", headers={"X-Requested-With": "fetch"})
    assert b"<html" not in b.data and b.headers["X-Total-Count"] == "1"
    assert b.get_data(as_text=True).count('class="card"') == 1


def test_query_counts_stay_small_and_tables_are_not_rechecked(big):
    big.get("/board")                                                # warm: tables already checked in this process
    assert qcount(big.get("/board")) <= 4                            # leads, tracker, edits, manual
    assert qcount(big.get("/list")) <= 4
    assert qcount(big.get("/interview")) <= 1                        # one aggregate query for the sidebar numbers
    assert qcount(big.get("/research")) == 0                         # the public form never touches leads
    r = big.get(f"/lead?key={quote('c9.dev|p9@c9.dev')}")
    assert r.status_code == 200 and qcount(r) <= 6                   # keyed lookups, not "load all 5,000"
    assert "Server-Timing" in r.headers and "db;dur=" in r.headers["Server-Timing"]


def test_lists_leave_out_the_long_text_but_the_profile_has_it(big):
    light = db.all_leads()
    assert light[0]["draft_body"] is None and light[0]["devrel_evidence"] is None
    full = db.get_lead("c9.dev|p9@c9.dev")
    assert full["draft_body"].startswith("Hello") and full["devrel_evidence"].startswith("community")
    assert b"Hello Hello" in big.get(f"/lead?key={quote('c9.dev|p9@c9.dev')}").data
    assert b"Hello Hello" not in big.get("/list").data               # not shipped to the browser in lists
    csv = big.get("/export.csv?q=c9.dev").get_data(as_text=True)
    assert "Hello Hello" in csv                                       # the export still has every column


def test_no_stale_data_after_a_write(big):
    big.post("/tracker/status", json={"key": "c10.dev|p10@c10.dev", "status": "replied"}, headers={"X-CSRF": "tok"})
    assert "Company 00010" in big.get("/list?status=replied").get_data(as_text=True)
    assert "Company 00010" not in big.get("/list?status=queued").get_data(as_text=True)
    big.post("/tracker/status", json={"key": "c10.dev|p10@c10.dev", "status": "not contacted"}, headers={"X-CSRF": "tok"})
    assert "Company 00010" not in big.get("/list?status=replied").get_data(as_text=True)


# ---------------------------------------------------------------- concurrent edits
def test_two_people_saving_the_same_lead_cannot_silently_overwrite_each_other(big):
    k = "c20.dev|p20@c20.dev"
    form = lambda **kw: big.post("/tracker", data={"csrf": "tok", "key": k, "contact_status": "contacted", **kw})
    v0 = db.get_lead(k)["updated_at"] or ""
    assert form(notes="first person", v=v0).status_code == 302                  # opens the form, saves first
    assert db.get_lead(k)["notes"] == "first person"
    r = form(notes="second person (stale form)", v=v0)                          # same old form, saves second
    assert db.get_lead(k)["notes"] == "first person"                            # not overwritten
    page = big.get(r.headers["Location"]).get_data(as_text=True)
    assert "Someone changed this lead" in page
    v1 = db.get_lead(k)["updated_at"]
    assert form(notes="second person, after reload", v=v1).status_code == 302   # reloading gives a fresh version: saves fine
    assert db.get_lead(k)["notes"] == "second person, after reload"


def test_stage_change_between_open_and_save_is_detected_but_stage_menus_never_clobber_notes(big):
    k = "c21.dev|p21@c21.dev"
    big.post("/tracker", data={"csrf": "tok", "key": k, "contact_status": "contacted", "notes": "keep me"})
    v = db.get_lead(k)["updated_at"]
    big.post("/tracker/status", json={"key": k, "status": "replied"}, headers={"X-CSRF": "tok"})   # someone drags the card
    big.post("/tracker", data={"csrf": "tok", "key": k, "contact_status": "contacted", "notes": "stale", "v": v})
    l = db.get_lead(k)
    assert (l["contact_status"], l["notes"]) == ("replied", "keep me")           # the stale form lost; nothing was overwritten


def test_two_edits_of_contact_details_conflict_the_same_way(big):
    k = "c22.dev|p22@c22.dev"
    v0 = db.get_lead(k)["edit_updated_at"]
    ok = big.post("/lead/edit", data={"csrf": "tok", "key": k, "contact_name": "A", "contact_title": "t", "v": v0})
    bad = big.post("/lead/edit", data={"csrf": "tok", "key": k, "contact_name": "B", "contact_title": "t", "v": v0})
    assert ok.status_code == bad.status_code == 302
    assert db.get_lead(k)["contact_name"] == "A"
    assert "Someone changed this lead" in big.get(bad.headers["Location"]).get_data(as_text=True)


def test_first_ever_save_race_is_also_safe(big):
    k = "c23.dev|p23@c23.dev"
    assert (db.get_lead(k)["updated_at"] or "") == ""
    big.post("/tracker", data={"csrf": "tok", "key": k, "contact_status": "queued", "notes": "winner", "v": ""})
    big.post("/tracker", data={"csrf": "tok", "key": k, "contact_status": "queued", "notes": "loser", "v": ""})
    assert db.get_lead(k)["notes"] == "winner"


def test_a_form_without_a_version_still_saves(big):
    k = "c24.dev|p24@c24.dev"
    assert big.post("/tracker", data={"csrf": "tok", "key": k, "contact_status": "queued", "notes": "n"}).status_code == 302
    assert db.get_lead(k)["notes"] == "n"
