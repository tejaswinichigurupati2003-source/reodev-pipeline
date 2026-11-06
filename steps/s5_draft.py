"""Step 5 - Draft one cold email per contact. DRAFTS ONLY: nothing in this project can send email.

Output: data/<domain>/step5_drafts.json  {"drafts": {email: {subject, body, hook_quote, hook_source_url, no_hook_reason, flags, needs_review}}}
The LLM writes; CODE enforces: first-name greeting, subject <= 7 words, 60-110 words, 15-minute ask, banned words
(Zoom / acquisition / discounts / numbers ...), and the hook quote must really appear on the page it cites
(otherwise the hook is removed and the draft is flagged). The sign-off is added by code from SENDER_NAME.
"""
import json
import os
import re

import llm
import pipeline_io as io

OUTPUT_FILE = "step5_drafts.json"
BANNED = ["zoom", "acqui", "common room", "commonroom", "sunset", "shutting down", "discount", "% off", "free trial",
          "guarantee", "100%", "revolutionary", "game-changing", "synergy", "i hope this finds you"]
SCHEMA = {
    "type": "object",
    "properties": {"subject": {"type": "string"}, "body": {"type": "string"}, "hook_quote": {"type": "string"},
                   "hook_source_url": {"type": "string"}, "no_hook_reason": {"type": "string"}},
    "required": ["subject", "body", "hook_quote", "hook_source_url", "no_hook_reason"],
}


def _norm(t):
    return re.sub(r"\s+", " ", t or "").strip().lower()


def check(draft, first_name, raw):
    """Return (clean_draft, flags). Removes an unverifiable hook; flags every rule that failed."""
    flags = []
    body, subject = draft["body"].strip(), draft["subject"].strip()
    words = len(body.split())
    if not body.lower().startswith(f"hi {first_name.lower()},"):
        flags.append(f"greeting is not 'Hi {first_name},'")
    if len(subject.split()) > 7:
        flags.append("subject is longer than 7 words")
    if not 60 <= words <= 110:
        flags.append(f"body is {words} words (want 60-110)")
    if "15" not in body:
        flags.append("no 15-minute ask")
    low = (subject + " " + body).lower()
    flags += [f"banned phrase: {b!r}" for b in BANNED if b in low]
    quote = draft.get("hook_quote", "")
    # numbers only count when they stand alone (not "n8n"), and the verified hook quote may contain its own numbers
    checked = body.replace(quote, "") if quote else body
    checked = re.sub(r"\b15[- ]?min(?:ute)?s?\b", "", checked)
    if re.search(r"(?<![A-Za-z0-9])\d", checked):
        flags.append("contains a number that is not traceable")
    if quote:
        page = (raw.get("pages") or {}).get(draft.get("hook_source_url"))
        if not (page and _norm(quote) in _norm(page.get("text"))):
            flags.append("hook quote not found on the cited page: hook removed")
            draft = {**draft, "hook_quote": "", "hook_source_url": "", "no_hook_reason": "model's quote could not be verified"}
    return draft, flags


def _prompt(person, extracted):
    company = {k: extracted.get(k) for k in ("company", "product_summary", "company_type", "use_case_for_intent", "headcount")}
    company["developer_signals"] = [{"signal": s["signal"], "quote": s["evidence_quote"], "page": s["source_url"]}
                                    for s in extracted.get("devrel_signals", [])]
    persona = person.get("persona") or "go-to-market"
    if "common room" in persona.lower():  # drafts must never mention Common Room: use a neutral role instead
        persona = "go-to-market"
    values = {"persona": persona, "first_name": person["name"].split()[0],
              "person": json.dumps({"name": person["name"], "title": person.get("title")}, ensure_ascii=False),
              "company": json.dumps(company, ensure_ascii=False)}
    return re.sub(r"\{(persona|first_name|person|company)\}", lambda m: values[m.group(1)], io.load_prompt("s5_email.md"))


def run(domain, fresh=False):
    contacts = (io.load_json(domain, "step4_contacts.json") if io.exists(domain, "step4_contacts.json") else {}).get("contacts") or []
    drafts = {}
    if io.exists(domain, OUTPUT_FILE) and not fresh:
        drafts = io.load_json(domain, OUTPUT_FILE).get("drafts", {})
        if all(c.get("email") in drafts or not (c.get("email") and c.get("name")) for c in contacts):
            return {"domain": domain, "drafts": drafts, "sent": False}  # everyone already has a draft
    extracted, raw = io.load_json(domain, "step2_extracted.json"), io.load_json(domain, "step1_raw.json")
    sender = os.getenv("SENDER_NAME") or "[Your name]"
    for person in contacts:
        if not (person.get("email") and person.get("name")) or person["email"] in drafts:
            continue
        try:
            d = llm.generate_json(_prompt(person, extracted), SCHEMA, temperature=0.3)
            d, flags = check(d, person["name"].split()[0], raw)
            d["body"] = f"{d['body'].strip()}\n\n{sender}\nReo.Dev"
            drafts[person["email"]] = {**d, "flags": flags, "needs_review": bool(flags)}
        except Exception as exc:  # one failed draft must not lose the others
            drafts[person["email"]] = {"subject": "", "body": "", "flags": [f"draft failed: {exc}"], "needs_review": True}
    result = {"domain": domain, "drafts": drafts, "sent": False}
    io.save_json(domain, OUTPUT_FILE, result)
    return result


if __name__ == "__main__":
    import sys

    print(json.dumps(run(sys.argv[1], fresh="--fresh" in sys.argv), indent=2))
