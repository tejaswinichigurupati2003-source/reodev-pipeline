"""Role-fit scoring: who is a good research-interview target (DevRel, community, developer marketing, growth) and who is not."""
import importlib
import re

import pytest

import db
import pipeline_io
import personas
from steps import s4_contacts

BEST = ["Head of Developer Relations", "Senior Developer Advocate", "Open Source Community Outreach Lead", "Head of Community",
        "Product Marketing Manager, Developer Experience", "Developer Marketing Manager", "Community Manager"]
STRONG = ["Head of Growth", "Growth Marketing Manager", "Director of Growth", "Product-Led Growth Lead"]
POSSIBLE = ["Marketing Operations Manager", "Director of RevOps", "Global ABM Manager", "Senior Product Marketing Manager", "Pipeline Operations",
            "Demand Generation Lead", "Revenue Operations Analyst"]
UNLIKELY = ["Talent Acquisition Partner", "Technical Recruiter", "Director of Sales", "VP of Sales", "Head of Business Development",
            "Director of Sales Development", "Chief Revenue Officer", "CRO", "Director of Event Marketing", "Event Marketing Manager",
            "Director of Performance Marketing", "Paid Media Manager", "Brand Manager", "CMO", "Chief Executive Officer", "CFO", "General Counsel",
            "Social Media Community Manager", "Regional Director - Commercial Growth(APAC)", "Regional Director, Enterprise Growth",
            "Account Executive", "Director, Talent Acquisition - GTM", "Vice President, Global Event Marketing", "Co-Founder & CEO",
            "DEI Community Leader, Latine Employee Resource Group", "Director, Product Strategy & Inorganic Growth",
            "Senior Lifecycle Marketing Manager, Lifecycle + Paid Growth"]


@pytest.mark.parametrize("title", BEST)
def test_best_fits(title):
    assert personas.interview_fit(title)["score"] == 4


@pytest.mark.parametrize("title", STRONG)
def test_growth_is_strong(title):
    assert personas.interview_fit(title)["score"] == 3


@pytest.mark.parametrize("title", POSSIBLE)
def test_operations_roles_are_possible(title):
    assert personas.interview_fit(title)["score"] == 2


@pytest.mark.parametrize("title", UNLIKELY)
def test_the_people_you_flagged_are_unlikely(title):
    r = personas.interview_fit(title)
    assert r["score"] == 0 and r["reason"], title


def test_founders_depend_on_company_size():
    assert personas.interview_fit("Co-founder")["score"] == 0                          # unknown size: assume big (the Snowflake and Vercel case)
    assert personas.interview_fit("Co-founder", "5,000 employees")["score"] == 0
    assert personas.interview_fit("Co-founder and CEO", "51-200")["score"] == 0
    assert personas.interview_fit("Co-founder", "12 people")["score"] == 1             # tiny company: hands-on, but still busy


def test_missing_titles_and_unrelated_roles():
    assert personas.interview_fit("")["score"] == 1 and "unknown" in personas.interview_fit("")["reason"].lower()
    assert personas.interview_fit("Staff Software Engineer")["score"] == 1
    assert personas.interview_fit("Event Marketing Manager")["score"] < personas.interview_fit("Marketing Manager")["score"]


# ---------------------------------------------------------------- contact search only spends credits on good targets
def test_credits_are_only_spent_on_good_targets():
    want = s4_contacts.make_want("201-500")
    for t in UNLIKELY + ["Co-founder"]:
        assert want(t) is None, t
    best, strong, ops = want("Head of Developer Relations"), want("Head of Growth"), want("Marketing Operations Manager")
    assert best < strong < ops                                                         # best roles are enriched first
    assert want("Staff Engineer") is None


def test_hunter_path_rejects_and_ranks_the_same_way():
    titles = ["VP of Sales", "Talent Acquisition Partner", "Head of Growth", "Head of Community", "Co-founder", "Director of Event Marketing"]
    people = [{"value": f"{i}@x.dev", "first_name": "A", "last_name": f"B{i}", "position": t, "confidence": 95} for i, t in enumerate(titles)]
    contacts, rejected = s4_contacts.pick_contacts(people, "201-500")
    assert [c["title"] for c in contacts] == ["Head of Community", "Head of Growth"]                    # best first, nobody unlikely
    assert all("poor interview target" in r["why"] for r in rejected) and len(rejected) == 4


# ---------------------------------------------------------------- the board and list rank by role
def lead(i, title, tier="A", score=70):
    r = {c: None for c in db.LEAD_COLUMNS}
    r.update({"rank": i, "tier": tier, "score": score, "company": f"Company {i:02d}", "domain": f"c{i}.dev", "account_status": "verified_public",
              "how_identified": "t", "company_type": "devtool", "fit": 1, "value": 1, "switching_proxy": 1, "ease": 1, "headcount": "201-500",
              "contact_name": f"Person {i}", "contact_title": title, "email": f"p{i}@c{i}.dev", "email_confidence": 90, "contact_source": "t"})
    return r


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline_io, "DATA_DIR", tmp_path)
    for k in ("DATABASE_URL", "DASHBOARD_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("DASHBOARD_PUBLIC", "1")
    rows = [lead(1, "VP of Sales", score=99), lead(2, "Director of Event Marketing", score=95), lead(3, "Head of Growth", score=60),
            lead(4, "Head of Developer Relations", score=40), lead(5, "Marketing Operations Manager", score=80),
            lead(6, "Talent Acquisition Lead", score=90), lead(7, "Co-founder", score=98), lead(8, "Community Manager", tier="C", score=10)]
    rows.append({**lead(9, None, score=50), "contact_name": None, "email": None})            # an account with no contact yet
    db.sync(rows)
    import app as appmod
    return importlib.reload(appmod).app.test_client()


def order(client, url):
    html = client.get(url).get_data(as_text=True)
    return [int(n) for n in dict.fromkeys(re.findall(r"Company (\d\d)", html))]


def test_default_order_puts_the_best_roles_first_whatever_the_company_score(client):
    got = order(client, "/list")
    assert got[:2] == [4, 8]                                   # DevRel and Community first, even though their companies score lowest
    assert got.index(3) == 2 and got.index(5) == 3             # then growth, then operations
    assert set(got[4:8]) == {1, 2, 6, 7} and got[-1] == 9      # sales VP, events, recruiter, founder after; no contact last
    assert order(client, "/list?sort=rank")[0] != 4            # the old company-fit order is still available


def test_best_targets_and_hide_unlikely_filters(client):
    assert set(order(client, "/list?fit=best")) == {3, 4, 8}
    shown = set(order(client, "/list?fit=hide"))
    assert shown == {3, 4, 5, 8, 9} and not shown & {1, 2, 6, 7}
    assert set(order(client, "/board?fit=best")) == {3, 4, 8}
    assert set(order(client, "/board?fit=best&tier=C")) == {8}


def test_every_card_and_row_shows_the_role_label_and_reason(client):
    board = client.get("/board").get_data(as_text=True)
    assert "rolefit rf-4" in board and "rolefit rf-0" in board and "Best fit" in board and "Unlikely" in board
    assert 'title="Senior sales leader: busy and rarely the daily user"' in board
    assert "Executive or founder at a larger company" in board
    profile = client.get("/lead?key=c1.dev%7Cp1%40c1.dev").get_data(as_text=True)
    assert "Interview fit" in profile and "Senior sales leader" in profile


def test_sidebar_shows_how_many_best_targets_exist(client):
    html = client.get("/list").get_data(as_text=True)
    assert re.search(r'Best interview targets <span class="n">3</span>', html)


def test_editing_a_title_changes_the_fit_immediately(client):
    key = "c1.dev|p1@c1.dev"
    assert db.get_lead(key)["role_score"] == 0
    client.get("/board")
    with client.session_transaction() as s:
        s["csrf"] = "t"
    client.post("/lead/edit", data={"csrf": "t", "key": key, "contact_name": "Person 1", "contact_title": "Head of Developer Relations", "email": "p1@c1.dev"})
    assert db.get_lead(key)["role_score"] == 4
