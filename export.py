"""Builds data/leads.csv, one row per (account, contact), ranked by score. This is the outreach tracker.

Columns separate what is VERIFIED (quotes, Hunter data) from what is an ASSUMPTION (the switching score),
and leave empty tracker columns (outreach/interview status) for the team to fill in.
Text that a spreadsheet could run as a formula is neutralised.
"""
import csv
import json

import pipeline_io as io

COLUMNS = ["rank", "tier", "score", "company", "domain", "account_status", "how_identified", "source_url",
           "company_type", "headcount", "fit", "value", "switching_proxy", "ease", "devrel_evidence",
           "contact_name", "contact_title", "persona", "email", "email_confidence", "contact_source", "linkedin_search",
           "draft_subject", "draft_body", "draft_flags",
           "contact_status", "last_touch", "channel", "reply", "interview_done", "notes"]


def _safe(v):
    v = "" if v is None else str(v)
    return "'" + v if v and v[0] in "=+-@\t\r" else v


def _load(domain, name):
    return io.load_json(domain, name) if io.exists(domain, name) else None


def build_rows(accounts):
    rows = []
    for a in accounts:
        d = a.get("domain")
        sc = _load(d, "step3_score.json") if d else None
        ex = _load(d, "step2_extracted.json") if d else None
        if not sc or not ex:
            continue
        ct = _load(d, "step4_contacts.json") or {}
        evidence = " | ".join(f"{s['signal']}: \"{s['evidence_quote']}\" ({s['source_url']})" for s in ex.get("devrel_signals", []))
        base = {"tier": sc["tier"], "score": sc["total"], "company": a.get("name") or ex.get("company"), "domain": d,
                "account_status": a.get("status"), "how_identified": a.get("how_identified"), "source_url": a.get("source_url"),
                "company_type": ex.get("company_type"), "headcount": ex.get("headcount"), "fit": sc["fit"], "value": sc["value"],
                "switching_proxy": sc["switching"], "ease": sc["ease"], "devrel_evidence": evidence,
                "linkedin_search": ct.get("linkedin_search", ""), "contact_status": "not contacted"}
        people = ct.get("contacts") or [{}]
        drafts = (_load(d, "step5_drafts.json") or {}).get("drafts", {})
        for p in people:
            dr = drafts.get(p.get("email"), {})
            rows.append({**base, "draft_subject": dr.get("subject"), "draft_body": dr.get("body"),
                         "draft_flags": "; ".join(dr.get("flags", [])),"contact_name": p.get("name"), "contact_title": p.get("title"), "persona": p.get("persona"),
                         "email": p.get("email"), "email_confidence": p.get("hunter_confidence"), "contact_source": p.get("source")})
    order = {"A": 0, "B": 1, "C": 2, "SKIP": 3}
    rows.sort(key=lambda r: (order[r["tier"]], -r["score"], r["domain"]))
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return rows


def write_leads(accounts):
    rows = build_rows(accounts)
    path = io.DATA_DIR / "leads.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:  # utf-8-sig so Excel opens it cleanly
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow({c: _safe(r.get(c)) for c in COLUMNS})
    accts = {r["domain"] for r in rows}
    by_tier = {}
    for t in ("A", "B", "C", "SKIP"):
        by_tier[t] = len({r["domain"] for r in rows if r["tier"] == t})
    with_email = len({r["domain"] for r in rows if r.get("email")})
    summary = f"{len(accts)} accounts scored {json.dumps(by_tier)}; {with_email} have at least one contact email"
    return path, summary
