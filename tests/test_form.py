"""The interactive research form: wording, dropdowns, chips, storage, errors, responses page, CSV, and upgrading old data."""
import importlib
import json
import re
import sqlite3

import pytest

import db
import pipeline_io
import research_form as rf

TEAM_WORDING = [
    "How does your team use Common Room day to day, and who uses it?",
    "What decision or action does it help you make? Can you give a recent example?",
    "How did you choose it, and what else did you consider?",
    "What does it do well that you’d hate to lose?",
    "Where does it fall short, or what do you work around with spreadsheets or other tools?",
    "How do you currently know which developers or companies are ready to buy?",
    "How does the data reach sales or your CRM, and does that work smoothly?",
    "When does your contract renew, and who decides whether you keep the tool?",
    "What would make you evaluate an alternative, and what would stop you?",
    "Who else should we talk to?",
]
BASE = {"name": "Sam Ng", "email": "sam@example.com", "company": "Example", "consent": "yes"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline_io, "DATA_DIR", tmp_path)
    for k in ("DATABASE_URL", "DASHBOARD_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("DASHBOARD_PUBLIC", "1")
    monkeypatch.setenv("RESPONSES_KEY", "k")
    import app as appmod
    c = importlib.reload(appmod).app.test_client()
    return c


def send(c, data, json_mode=True):
    return c.post("/research", data=data, headers={"X-Requested-With": "fetch"} if json_mode else {})


def stored():
    return db.list_responses()


# ---------------------------------------------------------------- the page
def test_page_has_the_teams_ten_questions_word_for_word(client):
    html = client.get("/research").get_data(as_text=True)
    for q in TEAM_WORDING:
        assert q.replace("’", "’") in html.replace("&#39;", "'") or q in html, q
    assert [q["title"] for q in rf.QUESTIONS] == TEAM_WORDING
    assert html.count('class="step') >= 13 and 'data-id="finish"' in html                      # intro + about you + 10 questions + review
    assert "Question 10 of 10" in html and "Skip" in html


def test_dropdowns_and_chips_are_real_form_controls(client):
    html = client.get("/research").get_data(as_text=True)
    for option in ("Salesforce", "HubSpot", "Within 3 months", "Leadership or founders", "More than 2 years", "Every day",
                   "Revenue operations", "VP or head of", "51 to 200", "Weekday afternoons", "Singapore / Asia Pacific"):
        assert f"<option >{option}</option>" in html or f">{option}</option>" in html, option
    for name in ("q1_length", "q1_often", "q7_crm", "q8_renewal", "q8_decider", "p_function", "p_seniority", "p_size", "p_window", "p_zone"):
        assert f'<select id="{name}"' in html or f'name="{name}"' in html, name
    assert html.count('type="checkbox" name="q9_triggers"') == 7 and html.count('type="checkbox" name="q9_blockers"') == 6
    assert 'name="ref_name_3"' in html and 'name="website"' in html                              # referrals and the bot trap


def test_works_without_javascript_and_adapts_with_it(client):
    html = client.get("/research").get_data(as_text=True)
    assert "document.documentElement.className='js'" in html          # steps only hide themselves once JavaScript has run
    assert re.search(r"\.js \.step\{display:none", html) and ".nav{display:none" in html     # without JS everything is visible and the form still posts
    assert 'method="post"' in html and 'id="send"' in html
    for q in rf.QUESTIONS:
        assert f'data-alt="{q["alt"]}"'.replace("&", "&amp;") in html.replace("&#39;", "'") or q["alt"][:30] in html   # wording for people who have not used it


def test_non_javascript_error_keeps_everything_they_typed(client):
    r = send(client, {**BASE, "name": "", "q1": "daily use", "q1_who": ["Marketing", "Founders"], "q7_crm": "HubSpot", "p_zone": "US Eastern"}, json_mode=False)
    html = r.get_data(as_text=True)
    assert r.status_code == 400 and "name and company" in html
    assert "daily use" in html and re.search(r'value="Marketing"\s+checked', html) and re.search(r"<option selected>HubSpot</option>", html)


# ---------------------------------------------------------------- saving
def test_full_submission_is_stored_and_shown(client):
    data = {**BASE, "role": "Head of Growth", "cr_status": "I use it now", "p_function": "Marketing", "p_size": "51 to 200", "p_zone": "India (IST)",
            "q1": "We use it every morning", "q1_who": ["Marketing", "Sales / SDRs"], "q1_length": "1 to 2 years", "q1_often": "Every day",
            "q2": "<script>alert(1)</script> found a lead", "q7_crm": "HubSpot", "q7_route": ["CSV exports"], "q8_renewal": "3 to 6 months",
            "q9_triggers": ["Missing integration", "Pricing or packaging change"], "q9_blockers": ["Migration effort"],
            "ref_name_1": "Ann Lee", "ref_contact_1": "ann@x.com", "availability": "Tue pm", "p_window": "Weekday afternoons", "quote_ok": "yes"}
    r = send(client, data)
    assert r.get_json() == {"ok": True}
    row = stored()[0]
    parsed = rf.loads(row["answers"])
    assert parsed["answers"]["q1"] == {"text": "We use it every morning", "chips": {"who": ["Marketing", "Sales / SDRs"], "length": ["1 to 2 years"], "often": ["Every day"]}}
    assert parsed["answers"]["q9"]["chips"] == {"triggers": ["Missing integration", "Pricing or packaging change"], "blockers": ["Migration effort"]}
    assert parsed["answers"]["q10"]["referrals"] == [{"name": "Ann Lee", "contact": "ann@x.com"}]
    assert parsed["profile"] == {"function": "Marketing", "size": "51 to 200", "zone": "India (IST)", "window": "Weekday afternoons"}
    assert parsed["quote_ok"] is True and row["cr_status"] == "I use it now"
    client.get("/responses?key=k")
    page = client.get("/responses").get_data(as_text=True)
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page and "<script>alert(1)" not in page          # shown as text, never run
    assert "We use it every morning" in page and "Missing integration" in page and "Ann Lee" in page and "OK to quote anonymously." in page


def test_a_person_can_just_ask_to_be_contacted_with_no_answers(client):
    assert send(client, BASE).get_json() == {"ok": True}
    row = stored()[0]
    assert rf.loads(row["answers"]).get("answers") == {}
    client.get("/responses?key=k")
    page = client.get("/responses").get_data(as_text=True)
    assert "only asked to be contacted" in page and "Do not quote them." in page


def test_made_up_values_are_dropped_and_long_text_is_capped(client):
    send(client, {**BASE, "q1": "x" * 5000, "q1_who": ["Marketing", "Hacker"], "q1_length": "Forever", "p_zone": "Mars", "q8_renewal": ["Not sure", "Within 3 months"]})
    a = rf.loads(stored()[0]["answers"])
    assert len(a["answers"]["q1"]["text"]) == rf.MAX_TEXT and a["answers"]["q1"]["chips"] == {"who": ["Marketing"]}
    assert "profile" not in a and a["answers"]["q8"]["chips"] == {"renewal": ["Not sure"]}          # single-choice keeps one value


def test_errors_come_back_as_json_the_page_can_show(client):
    for bad, msg in [({**BASE, "name": ""}, "name and company"), ({**BASE, "email": "nope"}, "valid email"),
                     ({k: v for k, v in BASE.items() if k != "consent"}, "tick the box")]:
        r = send(client, bad)
        assert r.status_code == 400 and msg in r.get_json()["error"] and r.get_json()["ok"] is False
    assert stored() == []
    assert send(client, {**BASE, "website": "http://spam"}).get_json() == {"ok": True} and stored() == []      # bots get a fake success


# ---------------------------------------------------------------- CSV and upgrading old data
def test_csv_has_every_answer_as_a_column_and_neutralises_formulas(client):
    send(client, {**BASE, "company": "=HYPERLINK(1)", "q3": "chose it after a trial", "q5_gaps": ["Spreadsheets", "Pricing"], "p_function": "Sales"})
    client.get("/responses?key=k")
    text = client.get("/responses.csv").get_data(as_text=True)
    header = text.splitlines()[0].lstrip("﻿").split(",")
    assert all(q["id"] in header for q in rf.QUESTIONS) and "p_function" in header and "quote_ok" in header
    assert "chose it after a trial" in text and "Gaps" not in text and "Common workarounds: Spreadsheets; Pricing" in text
    assert "'=HYPERLINK(1)" in text


def test_an_existing_table_is_upgraded_without_losing_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline_io, "DATA_DIR", tmp_path)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    con = sqlite3.connect(tmp_path / "local.db")
    con.execute("CREATE TABLE research_responses (id TEXT PRIMARY KEY, created_at TEXT, name TEXT, email TEXT, company TEXT, role TEXT, "
                "cr_status TEXT, tools TEXT, availability TEXT, consent TEXT)")
    con.execute("INSERT INTO research_responses VALUES ('old1','2026-11-01T00:00:00','Old Person','old@x.com','Old Co','','','','','yes')")
    con.commit(); con.close()
    db._ready.clear()
    rows = db.list_responses()                                   # first use creates the missing column
    assert [r["name"] for r in rows] == ["Old Person"] and rows[0]["answers"] is None
    db.add_response({**BASE, "q1": "new style answer"}, "2026-11-06T10:00:00.000000+00:00")
    assert len(db.list_responses()) == 2
