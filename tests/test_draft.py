"""Invitation drafts: written by students about a class project, never as Reo.Dev, never a pitch, never invented."""
import pytest

import interview
import pipeline_io
from steps import s5_draft

RAW = {"pages": {}}
LEAD = {"contact_name": "Dana Lee", "contact_title": "Head of Developer Relations", "company": "Acme Dev"}


def test_the_invitation_says_student_and_never_speaks_as_reo_dev():
    subject, body, linkedin = interview.invite(LEAD, "https://x.test/research", "Tej")
    for text in (body, linkedin):
        low = text.lower()
        assert "student" in low and "not a sales call" in low                    # who we are and what this is
        assert "not a reo.dev employee" in low or "not an employee" in low        # the plain statement of who we are not
        assert "reo.dev" in low                                                   # honest: the case company is named, never hidden
    assert not any(line.strip() == "Reo.Dev" for line in body.splitlines())      # never signed as Reo.Dev
    assert body.rstrip().endswith("Tej\nMesa School of Business") and "https://x.test/research" in body
    assert subject.startswith("Student research") and "Acme Dev" in subject
    blob = (subject + body + linkedin).lower()
    for banned in ("zoom", "acqui", "discount", "% off", "customers include", "nvidia", "langchain", "n8n", "revenue intelligence platform"):
        assert banned not in blob, banned                                         # no pitch, no product claims, no customer names


def test_the_greeting_and_role_are_filled_in_and_missing_details_do_not_break_it():
    _, body, _ = interview.invite(LEAD, "u", "Tej")
    assert body.startswith("Hi Dana,") and "as Head of Developer Relations at Acme Dev" in body
    _, body2, _ = interview.invite({"company": "Acme Dev"}, "u", "Tej")
    assert body2.startswith("Hi there,") and "your experience at Acme Dev" in body2 and "{" not in body2


def _prepare(tmp_path, monkeypatch, signals):
    monkeypatch.setattr(pipeline_io, "DATA_DIR", tmp_path)
    monkeypatch.setenv("SENDER_NAME", "Tej")
    monkeypatch.delenv("RESEARCH_FORM_URL", raising=False)
    pipeline_io.save_json("acme.dev", "step2_extracted.json", {"company": "Acme Dev", "devrel_signals": signals})
    pipeline_io.save_json("acme.dev", "step4_contacts.json", {"contacts": [
        {"email": "dana@acme.dev", "name": "Dana Lee", "title": "Head of Developer Relations"},
        {"email": "noname@acme.dev", "name": None, "title": "x"}]})


def test_drafts_are_deterministic_student_invites_and_never_sent(tmp_path, monkeypatch):
    _prepare(tmp_path, monkeypatch, [])
    res = s5_draft.run("acme.dev")
    d = res["drafts"]["dana@acme.dev"]
    assert res["sent"] is False and set(res["drafts"]) == {"dana@acme.dev"}          # contacts without a name are skipped
    assert d["needs_review"] is False and d["flags"] == [] and d["hook_quote"] == ""
    assert "student" in d["body"] and "https://reodev-leads.vercel.app/research" in d["body"]
    assert "developer community" not in d["body"]                                    # no signal on file, so no personal touch invented
    assert s5_draft.run("acme.dev", fresh=True) == res                               # same input, same words: nothing random


def test_the_personal_touch_appears_only_when_the_company_really_has_a_community(tmp_path, monkeypatch):
    _prepare(tmp_path, monkeypatch, [{"signal": "Discord", "evidence_quote": "Join our Discord", "source_url": "https://acme.dev"}])
    d = s5_draft.run("acme.dev")["drafts"]["dana@acme.dev"]
    assert "I noticed Acme Dev has a public developer community" in d["body"]
    assert d["hook_quote"] == "Join our Discord" and d["hook_source_url"] == "https://acme.dev"


def test_the_form_link_can_be_pointed_at_a_neutral_address(tmp_path, monkeypatch):
    _prepare(tmp_path, monkeypatch, [])
    monkeypatch.setenv("RESEARCH_FORM_URL", "https://students.example.org/talk")
    assert "https://students.example.org/talk" in s5_draft.run("acme.dev")["drafts"]["dana@acme.dev"]["body"]
