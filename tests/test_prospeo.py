import pytest

import apollo
import hunter
import llm
import pipeline_io
import prospeo
from steps import s1_scrape, s2_extract, s3_score, s4_contacts

HOME = "Acme Dev builds an open-source API gateway for developers."
LLM_REPLY = {"company": "Acme Dev", "company_type": "devtool", "sells_to_developers": True, "headcount": "50-200 employees",
             "devrel_signals": [], "missing_info": [], "reasoning": "r"}
SEARCH = {"error": False, "results": [
    {"person": {"person_id": "p1", "first_name": "Dana", "last_name": "Lee", "current_job_title": "Head of Developer Relations"}},
    {"person": {"person_id": "p2", "first_name": "Sam", "last_name": "Ng", "current_job_title": "Staff Engineer"}},
    {"person": {"person_id": "p3", "first_name": "Gus", "last_name": "Ray", "current_job_title": "Head of Growth"}},
    {"person": {"person_id": "p4", "first_name": "Eva", "last_name": "Kim", "current_job_title": "Director of Marketing"}}]}
ENRICH = {"p1": {"error": False, "person": {"person_id": "p1", "email": {"status": "VERIFIED", "revealed": True, "email": "dana@acme.dev"}}},
          "p3": {"error": False, "person": {"person_id": "p3", "email": {"status": "VERIFIED", "revealed": True, "email": "gus*****@acme.dev"}}},
          "p4": {"error": True, "error_code": "NO_MATCH"}}


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline_io, "DATA_DIR", tmp_path)
    monkeypatch.setattr(s1_scrape.own_scraper, "fetch_doc", lambda url: {"markdown": HOME, "raw_html": "<p>x</p>"} if url == "https://acme.dev" else (_ for _ in ()).throw(RuntimeError("404")))
    monkeypatch.setattr(llm, "generate_json", lambda *a, **k: dict(LLM_REPLY))
    monkeypatch.setattr(prospeo, "_sleep", lambda s: None)
    for k in ("APOLLO_API_KEY", "HUNTER_API_KEY", "CONTACT_PROVIDER", "PROSPEO_MAX_ENRICH"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("PROSPEO_API_KEY", "k")


@pytest.fixture
def calls(monkeypatch):
    log = []

    def fake(path, body):
        log.append((path, body))
        if path == "/search-person":
            return 200, SEARCH
        d = ENRICH[body["data"]["person_id"]]
        return (200 if not d["error"] else 400), d
    monkeypatch.setattr(prospeo, "_request", fake)
    return log


def test_search_filters_and_only_best_two_enriched_then_cached(calls):
    want = s4_contacts.make_want("50-200")
    people = prospeo.find_people("acme.dev", want)
    assert [p["value"] for p in people] == ["dana@acme.dev"]  # p3's email came back masked: skipped; p4 no match
    search_body = calls[0][1]
    assert search_body["filters"]["company"]["websites"]["include"] == ["acme.dev"]
    enriched = [c[1]["data"]["person_id"] for c in calls if c[0] == "/enrich-person"]
    assert enriched == ["p1", "p3"]          # dev-relations first, then growth; engineer never enriched; capped at 2
    assert all(c[1]["only_verified_email"] for c in calls if c[0] == "/enrich-person")
    n = len(calls)
    prospeo.find_people("acme.dev", want)
    assert len(calls) == n                   # second call came from the cache


def test_no_results_is_not_an_error(monkeypatch):
    monkeypatch.setattr(prospeo, "_request", lambda p, b: (400, {"error": True, "error_code": "NO_RESULTS"}))
    assert prospeo.find_people("x.dev", lambda t: 1) == []


@pytest.mark.parametrize("code,text", [("INVALID_API_KEY", "rejected the API key"), ("INSUFFICIENT_CREDITS", "usage limit")])
def test_fatal_errors(monkeypatch, code, text):
    monkeypatch.setattr(prospeo, "_request", lambda p, b: (400, {"error": True, "error_code": code}))
    with pytest.raises(prospeo.ProspeoError, match=text):
        prospeo.search("x.dev")


def test_rate_limit_retries_then_fails(monkeypatch):
    n = []
    monkeypatch.setattr(prospeo, "_request", lambda p, b: (n.append(1), (429, {}))[1])
    with pytest.raises(prospeo.ProspeoError, match="3 attempts"):
        prospeo.search("x.dev")
    assert len(n) == 3


def test_chain_falls_through_to_hunter_when_prospeo_out_of_credits(monkeypatch):
    monkeypatch.setenv("HUNTER_API_KEY", "k")
    monkeypatch.setattr(prospeo, "_request", lambda p, b: (400, {"error": True, "error_code": "INSUFFICIENT_CREDITS"}))
    monkeypatch.setattr(hunter, "_request", lambda d: (200, {"data": {"emails": [
        {"value": "dana@acme.dev", "first_name": "Dana", "last_name": "Lee", "position": "Head of Developer Relations", "confidence": 90}]}}))
    s1_scrape.run("acme.dev"); s2_extract.run("acme.dev"); s3_score.run("acme.dev")
    res = s4_contacts.run("acme.dev")
    assert res["source"] == "hunter" and res["found"] and "prospeo failed" in res["provider_notes"][0]


def test_prospeo_is_used_when_it_is_the_only_key(calls):
    s1_scrape.run("acme.dev"); s2_extract.run("acme.dev"); s3_score.run("acme.dev")
    res = s4_contacts.run("acme.dev")
    assert res["source"] == "prospeo" and [c["email"] for c in res["contacts"]] == ["dana@acme.dev"]
    assert s4_contacts.is_cached("acme.dev")
