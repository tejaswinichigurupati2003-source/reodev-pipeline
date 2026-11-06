import importlib
import re

import pytest

import db
import interview
import pipeline_io

LEAD = {c: None for c in db.LEAD_COLUMNS}
LEAD.update({"rank": 1, "tier": "A", "score": 80, "company": "Acme Dev", "domain": "acme.dev", "account_status": "verified_public",
             "how_identified": "Named on a public page", "company_type": "devtool", "headcount": "50-200", "fit": 40, "value": 12,
             "switching_proxy": 20, "ease": 5, "devrel_evidence": 'community: "Join our Discord" (https://acme.dev) | x',
             "contact_name": "Dana Lee", "contact_title": "Head of Developer Relations",
             "persona": "DevRel / Community / Developer Marketing", "email": "dana@acme.dev", "email_confidence": 95,
             "contact_source": "prospeo", "linkedin_search": "https://www.linkedin.com/search/results/people/?keywords=x"})


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline_io, "DATA_DIR", tmp_path)
    for k in ("DATABASE_URL", "DASHBOARD_PASSWORD", "DASHBOARD_PUBLIC", "RESPONSES_KEY", "RESEARCH_CONTACT_EMAIL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("SENDER_NAME", "Tej")
    db.sync([LEAD])


def make_client(monkeypatch, **env):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    import app as appmod
    appmod = importlib.reload(appmod)
    return appmod.app.test_client()


GOOD = {"name": "Sam Ng", "email": "sam@example.com", "company": "Example", "role": "RevOps", "cr_status": "I use it now",
        "availability": "Tue pm", "consent": "yes"}


# ---------------------------------------------------------------- wording rules
def test_no_question_or_invite_mentions_zoom_or_acquisition():
    all_text = []
    for _, qs in interview.CORE:
        all_text += qs
    for qs in list(interview.USAGE.values()) + list(interview.PERSONA.values()):
        all_text += qs
    g = interview.build_guide(LEAD)
    all_text += [q for _, qs in g["sections"] for q in qs] + g["persona_questions"] + [q for v in g["branches"].values() for q in v]
    subject, body, li = interview.invite(LEAD, "https://x.test/research", "Tej")
    all_text += [subject, body, li]
    blob = " ".join(all_text).lower()
    for banned in ("zoom", "acqui", "will shut", "sunset", "discount code", "guarantee"):
        assert banned not in blob
    assert "reo.dev" in body.lower() and "not a sales call" in body.lower()      # honest about the case company and purpose


def test_guide_is_filled_in_and_picks_the_right_role_questions():
    g = interview.build_guide(LEAD)
    flat = " ".join(q for _, qs in g["sections"] for q in qs)
    assert "Acme Dev" in flat and "{company}" not in flat and "{first_name}" not in flat
    assert g["persona"] == "DevRel / Community / Developer Marketing"
    assert g["context"] and "Public developer-community signal" in " ".join(g["context"])
    # a person found by name only: persona comes from the title; unknown titles fall back to General
    assert interview.persona_key(None, "Director of Revenue Operations") == "RevOps / GTM Ops / Sales leader"
    assert interview.persona_key(None, "Chef") == "General"
    assert not re.search(r"\{\w+\}", interview.to_markdown(interview.build_guide({**LEAD, "contact_name": None, "contact_title": None})))


# ---------------------------------------------------------------- pages
def test_interview_pages_and_markdown(monkeypatch):
    c = make_client(monkeypatch, DASHBOARD_PUBLIC="1")
    assert "Interview questions" in c.get("/interview").get_data(as_text=True)
    page = c.get("/interview/guide?key=acme.dev|dana@acme.dev").get_data(as_text=True)
    assert "Dana Lee" in page and "Open in my email app" in page and "/research" in page and "Tej" in page
    md = c.get("/interview/guide?key=acme.dev|dana@acme.dev&format=md")
    assert md.mimetype == "text/markdown" and "# Interview guide: Dana Lee" in md.get_data(as_text=True)
    assert c.get("/interview/guide?key=nope|x").status_code == 404


def test_board_sidebar_links_never_carry_the_email_filter(monkeypatch):
    c = make_client(monkeypatch, DASHBOARD_PUBLIC="1")
    html = c.get("/board?tier=B&email=1").get_data(as_text=True)
    side = html.split("</aside>")[0]
    for href in re.findall(r'href="(/board[^"]*)"', side):
        assert "email=1" not in href          # a sidebar click always changes the view, whatever filters were on
    assert "Only with email: on" in html and "Clear filters" in html
    assert "Interview guide" in c.get("/board").get_data(as_text=True)


# ---------------------------------------------------------------- research form
def test_form_is_public_even_when_the_dashboard_has_a_password(monkeypatch):
    c = make_client(monkeypatch, DASHBOARD_PASSWORD="pw")
    assert c.get("/").status_code == 302                      # dashboard: locked
    assert c.get("/board").status_code == 302
    assert c.get("/research").status_code == 200              # form: open to invitees
    assert c.get("/research/thanks").status_code == 200
    assert c.get("/responses").status_code in (302, 404)      # responses: never open


def test_form_validation_messages(monkeypatch):
    c = make_client(monkeypatch, DASHBOARD_PUBLIC="1")
    for bad, msg in [({**GOOD, "name": ""}, "name and company"), ({**GOOD, "email": "nope"}, "valid email"),
                     ({k: v for k, v in GOOD.items() if k != "consent"}, "tick the box")]:
        r = c.post("/research", data=bad)
        assert r.status_code == 400 and msg in r.get_data(as_text=True)
    assert db.list_responses() == []
    r = c.post("/research", data={**GOOD, "name": "<script>alert(1)</script>", "company": "x" * 500})
    assert r.status_code == 302
    row = db.list_responses()[0]
    assert len(row["company"]) == 120 and "<script>" in row["name"]       # stored as text, length-capped


def test_honeypot_duplicates_and_flood_guard(monkeypatch):
    c = make_client(monkeypatch, DASHBOARD_PUBLIC="1")
    assert c.post("/research", data={**GOOD, "website": "http://spam"}).status_code == 302
    assert db.list_responses() == []                                       # bots get a fake thank-you, nothing stored
    c.post("/research", data=GOOD); c.post("/research", data=GOOD)
    assert len(db.list_responses()) == 1                                   # same email twice in a day: stored once
    for i in range(db.MAX_RESPONSES_PER_HOUR):
        c.post("/research", data={**GOOD, "email": f"p{i}@example.com"})
    r = c.post("/research", data={**GOOD, "email": "late@example.com"})
    assert r.status_code == 400 and "try again" in r.get_data(as_text=True)
    assert len(db.list_responses()) == db.MAX_RESPONSES_PER_HOUR


def test_responses_need_the_secret_key_even_in_public_mode(monkeypatch):
    c = make_client(monkeypatch, DASHBOARD_PUBLIC="1")
    c.post("/research", data={**GOOD, "name": "<b>Bold</b>"})
    assert c.get("/responses").status_code == 404                          # no RESPONSES_KEY configured: disabled
    c = make_client(monkeypatch, DASHBOARD_PUBLIC="1", RESPONSES_KEY="s3cret-key")
    assert c.get("/responses").status_code == 404 and c.get("/responses?key=wrong").status_code == 404
    assert c.get("/responses.csv").status_code == 404
    r = c.get("/responses?key=s3cret-key")
    assert r.status_code == 302 and "key=" not in r.headers["Location"]      # key is removed from the address bar
    page = c.get("/responses").get_data(as_text=True)
    assert "sam@example.com" in page and "&lt;b&gt;Bold&lt;/b&gt;" in page   # allowed now; HTML escaped
    csv_text = c.get("/responses.csv").get_data(as_text=True)
    assert "sam@example.com" in csv_text


def test_csv_neutralises_formulas(monkeypatch):
    c = make_client(monkeypatch, DASHBOARD_PUBLIC="1", RESPONSES_KEY="k")
    c.post("/research", data={**GOOD, "company": "=HYPERLINK(1)"})
    c.get("/responses?key=k")
    assert "'=HYPERLINK(1)" in c.get("/responses.csv").get_data(as_text=True)


def test_every_page_shares_the_same_shell(monkeypatch):
    c = make_client(monkeypatch, DASHBOARD_PUBLIC="1", RESPONSES_KEY="k")
    c.get("/responses?key=k")
    for url in ("/board", "/list", "/interview", "/interview/guide?key=acme.dev|dana@acme.dev", "/responses"):
        r = c.get(url)
        html = r.get_data(as_text=True)
        assert r.status_code == 200, url
        assert "class=\"side\"" in html and "Outreach board" in html and "href=\"/interview\"" in html, url
        assert "Tier A" in html and "Added by you" in html, url
    home = c.get("/", follow_redirects=True)
    assert home.status_code == 200 and "drag a card" in home.get_data(as_text=True)


def test_page_survives_a_database_hiccup(monkeypatch):
    c = make_client(monkeypatch, DASHBOARD_PUBLIC="1")
    import app as appmod
    monkeypatch.setattr(appmod.db, "list_leads", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db down")))
    assert c.get("/interview").status_code == 200  # sidebar numbers fall back to zero instead of an error page
