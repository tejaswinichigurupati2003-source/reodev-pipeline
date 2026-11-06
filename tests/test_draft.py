import pytest

import llm
import pipeline_io
from steps import s5_draft

RAW = {"pages": {"https://acme.dev": {"ok": True, "text": "We build an open-source API gateway. Join our community on Discord."}}}
GOOD_BODY = ("Hi Dana, I saw that Acme Dev runs an open-source gateway with an active Discord community. "
             "Reo.Dev turns developer activity such as GitHub stars, documentation visits and package installs into a clear list "
             "of the companies and people showing real interest, which helps a developer relations lead show what the community "
             "brings in. Would you be open to a 15 minute call to see whether it fits how your team works?")


def draft(**kw):
    base = {"subject": "Turning community activity into pipeline", "body": GOOD_BODY, "hook_quote": "Join our community on Discord",
            "hook_source_url": "https://acme.dev", "no_hook_reason": ""}
    return {**base, **kw}


def test_clean_draft_has_no_flags():
    d, flags = s5_draft.check(draft(), "Dana", RAW)
    assert flags == [] and d["hook_quote"]


def test_fabricated_hook_removed_and_flagged():
    d, flags = s5_draft.check(draft(hook_quote="We just raised a huge round"), "Dana", RAW)
    assert d["hook_quote"] == "" and any("hook removed" in f for f in flags)


@pytest.mark.parametrize("bad", ["Since Zoom bought them, many teams are unsure.", "We offer a 20% discount.", "Join 500 teams."])
def test_banned_content_flagged(bad):
    _, flags = s5_draft.check(draft(body=GOOD_BODY + " " + bad), "Dana", RAW)
    assert flags


def test_wrong_greeting_and_long_subject_flagged():
    _, flags = s5_draft.check(draft(body=GOOD_BODY.replace("Hi Dana,", "Dear Sir,"), subject="one two three four five six seven eight"), "Dana", RAW)
    assert len(flags) == 2


def test_run_writes_drafts_with_signoff_never_sent(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline_io, "DATA_DIR", tmp_path)
    monkeypatch.setenv("SENDER_NAME", "Tej")
    pipeline_io.save_json("acme.dev", "step1_raw.json", RAW)
    pipeline_io.save_json("acme.dev", "step2_extracted.json", {"company": "Acme Dev", "devrel_signals": []})
    pipeline_io.save_json("acme.dev", "step4_contacts.json", {"contacts": [{"email": "dana@acme.dev", "name": "Dana Lee", "title": "Head of DevRel", "persona": "DevRel"}]})
    monkeypatch.setattr(llm, "generate_json", lambda *a, **k: draft())
    res = s5_draft.run("acme.dev")
    d = res["drafts"]["dana@acme.dev"]
    assert d["body"].endswith("Tej\nReo.Dev") and not d["needs_review"] and res["sent"] is False


def test_failed_draft_is_isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline_io, "DATA_DIR", tmp_path)
    pipeline_io.save_json("a.dev", "step1_raw.json", RAW)
    pipeline_io.save_json("a.dev", "step2_extracted.json", {})
    pipeline_io.save_json("a.dev", "step4_contacts.json", {"contacts": [{"email": "x@a.dev", "name": "X Y", "title": "t"}]})
    monkeypatch.setattr(llm, "generate_json", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert s5_draft.run("a.dev")["drafts"]["x@a.dev"]["needs_review"] is True
