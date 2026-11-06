"""Step 5 - Draft an invitation for each contact. DRAFTS ONLY: nothing in this project can send email.

Output: data/<domain>/step5_drafts.json  {"drafts": {email: {subject, body, hook_quote, hook_source_url, flags, needs_review}}}

Who the message is from: STUDENTS doing a class project. It is never written as Reo.Dev, never signed "Reo.Dev", never a pitch.
It says plainly that Reo.Dev is the project's case company and that the sender is a student, not a Reo.Dev employee.
The text comes from interview.invite (fixed wording, no model, nothing invented). The only personal touch is one sentence, and only
when the company's own site really shows a developer community.
"""
import json
import os

import interview
import pipeline_io as io

OUTPUT_FILE = "step5_drafts.json"
DEFAULT_FORM_URL = "https://reodev-leads.vercel.app/research"


def form_url():
    """The public research form people are pointed to. Set RESEARCH_FORM_URL to use a different (for example neutral) address."""
    return os.getenv("RESEARCH_FORM_URL") or DEFAULT_FORM_URL


def run(domain, fresh=False):
    if io.exists(domain, OUTPUT_FILE) and not fresh:
        return io.load_json(domain, OUTPUT_FILE)
    contacts = (io.load_json(domain, "step4_contacts.json") if io.exists(domain, "step4_contacts.json") else {}).get("contacts") or []
    extracted = io.load_json(domain, "step2_extracted.json") if io.exists(domain, "step2_extracted.json") else {}
    company = extracted.get("company") or domain
    signals = extracted.get("devrel_signals") or []
    note = (f"I noticed {company} has a public developer community, which is part of why your view would help. " if signals else "")
    sender = os.getenv("SENDER_NAME") or "[Your name]"
    drafts = {}
    for person in contacts:
        if not (person.get("email") and person.get("name")):
            continue
        lead = {"contact_name": person["name"], "contact_title": person.get("title"), "company": company}
        subject, body, _ = interview.invite(lead, form_url(), sender, note=note)
        drafts[person["email"]] = {"subject": subject, "body": body, "flags": [], "needs_review": False,
                                   "hook_quote": signals[0]["evidence_quote"] if signals else "",
                                   "hook_source_url": signals[0]["source_url"] if signals else ""}
    result = {"domain": domain, "drafts": drafts, "sent": False}
    io.save_json(domain, OUTPUT_FILE, result)
    return result


if __name__ == "__main__":
    import sys

    print(json.dumps(run(sys.argv[1], fresh="--fresh" in sys.argv), indent=2))
