"""The research form's questions, in one place: the form page, the database, the responses page and the CSV all read this.

The wording is the team's own list (a main question plus nine). `alt` is the wording shown to people who have not used
Common Room, so the same form works for current users, past users, and people who only looked at it.
Quick-pick chips are neutral on purpose: they name things people already do, never a vendor, and never suggest an answer about the
future of any product. Pure code with no project imports, so the deployed dashboard can use it too.
"""
import json

MAX_TEXT = 2000
REFERRALS = 3
OPT_OUT = "Prefer not to say"

QUESTIONS = [
    {"id": "q1", "title": "How does your team use Common Room day to day, and who uses it?",
     "alt": "How does your team find and keep track of developer or community activity day to day, and who does it?",
     "hint": "A sentence or two is plenty.", "placeholder": "e.g. Our growth team checks it every morning for new sign-ups...",
     "groups": [{"name": "who", "label": "Who uses it?", "options": ["Marketing", "Sales / SDRs", "RevOps", "DevRel / Community", "Founders", "Product", "Just me"]},
                {"name": "length", "label": "How long have you used it?", "kind": "select", "single": True, "only_if": ["now", "before"],
                 "options": ["Less than 3 months", "3 to 12 months", "1 to 2 years", "More than 2 years"]},
                {"name": "often", "label": "How often does your team use it?", "kind": "select", "single": True, "only_if": ["now", "before"],
                 "options": ["Every day", "A few times a week", "Weekly", "Monthly", "Rarely"]}]},
    {"id": "q2", "title": "What decision or action does it help you make? Can you give a recent example?",
     "alt": "What decision or action does that help you make? Can you give a recent example?",
     "hint": "A short story is perfect: what happened, and what you did next.", "placeholder": "e.g. Last month we noticed a company's engineers all starred our repo, so we...", "groups": []},
    {"id": "q3", "title": "How did you choose it, and what else did you consider?",
     "alt": "How did you choose how to do this, and what else did you consider?",
     "hint": "Who was involved, and what tipped the decision?", "placeholder": "", "groups": []},
    {"id": "q4", "title": "What does it do well that you’d hate to lose?",
     "alt": "What works well today that you’d hate to lose?",
     "hint": "", "placeholder": "", "groups": []},
    {"id": "q5", "title": "Where does it fall short, or what do you work around with spreadsheets or other tools?",
     "alt": "Where does your current way of doing this fall short, or what do you work around with spreadsheets or other tools?",
     "hint": "Tap any that apply, then add detail if you like.", "placeholder": "",
     "groups": [{"name": "gaps", "label": "Common workarounds", "options": ["Spreadsheets", "Manual exports", "Data accuracy", "Missing integrations", "Pricing", "Reporting", "Nothing major"]}]},
    {"id": "q6", "title": "How do you currently know which developers or companies are ready to buy?",
     "alt": "How do you currently know which developers or companies are ready to buy?",
     "hint": "Tap the signals you rely on.", "placeholder": "",
     "groups": [{"name": "signals", "label": "Signals you use", "options": ["Product sign-ups", "GitHub activity", "Docs visits", "Community activity", "Website visits", "Rep knowledge", "An intent data tool"]}]},
    {"id": "q7", "title": "How does the data reach sales or your CRM, and does that work smoothly?",
     "alt": "How does that information reach sales or your CRM, and does that work smoothly?",
     "hint": "", "placeholder": "",
     "groups": [{"name": "crm", "label": "Which CRM do you use?", "kind": "select", "single": True,
                 "options": ["Salesforce", "HubSpot", "Pipedrive", "Attio", "Zoho", "Another CRM", "No CRM (spreadsheets)"]},
                {"name": "route", "label": "How it gets there", "options": ["Native CRM sync", "CSV exports", "Zapier / webhooks", "Slack alerts", "Manual copy-paste", "It doesn’t reach sales"]}]},
    {"id": "q8", "title": "When does your contract renew, and who decides whether you keep the tool?",
     "alt": "How are tools like this bought at your company, and who decides whether you keep one?",
     "hint": "Optional. Skip it if you’d rather not say.", "placeholder": "", "optional_note": True,
     "groups": [{"name": "renewal", "label": "Roughly when does it renew?", "kind": "select", "single": True,
                 "options": ["Within 3 months", "3 to 6 months", "6+ months", "Month to month", "Not sure", OPT_OUT]},
                {"name": "decider", "label": "Who decides whether you keep it?", "kind": "select", "single": True,
                 "options": ["Me", "My manager", "Leadership or founders", "A buying committee", "Procurement or finance", "Not sure", OPT_OUT]}]},
    {"id": "q9", "title": "What would make you evaluate an alternative, and what would stop you?",
     "alt": "What would make you look at a different approach or tool, and what would stop you?",
     "hint": "Tap any that apply to either side.", "placeholder": "",
     "groups": [{"name": "triggers", "label": "Would make me look", "options": ["Pricing or packaging change", "Missing integration", "Support quality", "Roadmap fit", "Data accuracy", "New team or budget", "Contract renewal"]},
                {"name": "blockers", "label": "Would stop me", "options": ["Contract term", "Integrations already in place", "Team habits", "Migration effort", "Security review", "No time"]}]},
    {"id": "q10", "title": "Who else should we talk to?", "alt": "Who else should we talk to?",
     "hint": "Only add people you are happy for us to contact. We will say you suggested them.", "placeholder": "", "groups": [], "referrals": True},
]
BY_ID = {q["id"]: q for q in QUESTIONS}

# Dropdowns on the first and last steps. Stored under "profile" in the answers.
PROFILE = [
    {"name": "function", "label": "Which team are you on?", "options": ["Marketing", "Sales", "Revenue operations", "DevRel / Community", "Product", "Engineering", "Founder / executive", "Other"]},
    {"name": "seniority", "label": "Seniority", "options": ["Individual contributor", "Manager", "Director", "VP or head of", "C-level or founder"]},
    {"name": "size", "label": "Company size (people)", "options": ["1 to 10", "11 to 50", "51 to 200", "201 to 1,000", "More than 1,000"]},
]
SCHEDULE = [
    {"name": "window", "label": "Best time for a 20-minute call", "options": ["Weekday mornings", "Weekday afternoons", "Weekday evenings", "Flexible"]},
    {"name": "zone", "label": "Your time zone", "options": ["India (IST)", "UK (GMT/BST)", "Central Europe (CET)", "US Eastern", "US Central", "US Pacific", "Singapore / Asia Pacific", "Australia (AEST)", "Other"]},
]
FIELD_GROUPS = PROFILE + SCHEDULE


def _list(form, key):
    """All values for a form key: works with Flask's MultiDict (getlist) and with a plain dict."""
    if hasattr(form, "getlist"):
        return form.getlist(key)
    v = form.get(key)
    return v if isinstance(v, list) else ([v] if v else [])


def parse(form):
    """Turn the submitted form into {"answers": {...}, "quote_ok": bool}. Only known questions and known chip values survive,
    and every text is trimmed and length-capped, so nothing unexpected reaches the database."""
    answers = {}
    for q in QUESTIONS:
        entry = {}
        text = (form.get(q["id"]) or "").strip()[:MAX_TEXT]
        if text:
            entry["text"] = text
        chips = {}
        for g in q["groups"]:
            picked = [v for v in _list(form, f"{q['id']}_{g['name']}") if v in g["options"]]
            picked = list(dict.fromkeys(picked))  # no repeats, order kept
            if g.get("single"):
                picked = picked[:1]
            if picked:
                chips[g["name"]] = picked
        if chips:
            entry["chips"] = chips
        if q.get("referrals"):
            refs = []
            for i in range(1, REFERRALS + 1):
                name = (form.get(f"ref_name_{i}") or "").strip()[:120]
                how = (form.get(f"ref_contact_{i}") or "").strip()[:200]
                if name or how:
                    refs.append({"name": name, "contact": how})
            if refs:
                entry["referrals"] = refs
        if entry:
            answers[q["id"]] = entry
    profile = {}
    for g in FIELD_GROUPS:
        v = (form.get(f"p_{g['name']}") or "").strip()
        if v in g["options"]:
            profile[g["name"]] = v
    out = {"answers": answers, "quote_ok": (form.get("quote_ok") or "") == "yes"}
    if profile:
        out["profile"] = profile
    return out


def dumps(parsed):
    return json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))


def loads(text):
    try:
        data = json.loads(text) if text else {}
    except ValueError:
        data = {}
    return data if isinstance(data, dict) else {}


def qa_list(parsed):
    """For display: [(question title, text, [(group label, [chips])], referrals)] for the questions that were answered."""
    out = []
    for q in QUESTIONS:
        a = (parsed.get("answers") or {}).get(q["id"])
        if not a:
            continue
        groups = [(g["label"], a["chips"][g["name"]]) for g in q["groups"] if a.get("chips", {}).get(g["name"])]
        out.append({"id": q["id"], "title": q["title"], "text": a.get("text", ""), "groups": groups, "referrals": a.get("referrals", [])})
    return out


def profile_list(parsed):
    """For display: [(label, value)] for the dropdowns the person filled in."""
    p = parsed.get("profile") or {}
    return [(g["label"], p[g["name"]]) for g in FIELD_GROUPS if p.get(g["name"])]


def flatten(parsed):
    """One plain-text cell per question, for the CSV: 'answer text | Label: chip; chip | Referrals: name (how)'."""
    row = {}
    for item in qa_list(parsed):
        parts = [item["text"]] if item["text"] else []
        parts += [f"{label}: {'; '.join(chips)}" for label, chips in item["groups"]]
        if item["referrals"]:
            parts.append("Referrals: " + "; ".join(f"{r['name']} ({r['contact']})" if r["contact"] else r["name"] for r in item["referrals"]))
        row[item["id"]] = " | ".join(parts)
    row["quote_ok"] = "yes" if parsed.get("quote_ok") else ""
    for g in FIELD_GROUPS:
        row["p_" + g["name"]] = (parsed.get("profile") or {}).get(g["name"], "")
    return row
