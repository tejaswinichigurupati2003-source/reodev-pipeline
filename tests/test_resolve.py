import discover


def test_candidates():
    assert discover.candidate_domains("Otter AI")[0] == "otterai.com"
    assert discover.candidate_domains("Twilio / Segment")[0] == "twilio.com"


def test_resolve_checks_page_mentions_name_and_flags_for_review():
    pages = {"https://notion.com": "Notion is the connected workspace", "https://notion.so": "x"}

    def fetch(url):
        if url in pages:
            return {"markdown": pages[url]}
        raise RuntimeError("not found")
    rows = [{"name": "Notion", "domain": "", "status": "needs_domain", "how_identified": "cr"},
            {"name": "Ghost Co", "domain": "", "status": "needs_domain", "how_identified": "cr"},
            {"name": "Asana", "domain": "asana.com", "status": "verified_public", "how_identified": "cr"}]
    assert discover.resolve_missing(rows, fetch) == 1
    assert (rows[0]["domain"], rows[0]["status"]) == ("notion.com", "domain_unconfirmed")
    assert "CONFIRM" in rows[0]["how_identified"]
    assert rows[1]["status"] == "needs_domain" and rows[2]["status"] == "verified_public"


def test_name_guard_blocks_wrong_company_for_guessed_domains(tmp_path, monkeypatch):
    import pipeline, pipeline_io
    monkeypatch.setattr(pipeline_io, "DATA_DIR", tmp_path)
    pipeline_io.save_json("circle.com", "step2_extracted.json", {"company": "Circle Internet Financial"})
    pipeline_io.save_json("circle.so", "step2_extracted.json", {"company": "Circle Community Platform"})
    pipeline_io.save_json("odd.io", "step2_extracted.json", {"company": "Totally Different Inc"})
    ok = lambda d, n, s: pipeline.names_match({"domain": d, "name": n, "status": s})
    assert ok("circle.com", "Circle", "domain_unconfirmed")          # same first word: allowed (human must still confirm)
    assert not ok("odd.io", "Oddly", "domain_unconfirmed")           # different company: blocked
    assert ok("odd.io", "Oddly", "verified_public")                  # verified rows are never blocked
