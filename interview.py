"""Interview questions and invitation text for the research conversations.

Built from the Mesa Business Strategy Lab brief (Reo.Dev: winning market share after a competitor acquisition):
  A. Where is the opportunity?   -> who uses what, who decides, renewal timing
  B. What is likely to change?   -> switching triggers and barriers, tested WITHOUT leading the person
  C. Why choose Reo.Dev?         -> what would make an evaluation worthwhile, proof, offers
  D. How to reach and convert?   -> channels, who is involved, how they like to be approached
  E. Revenue                     -> budget, deal size, speed
Rules baked in (from the brief's credibility constraint):
  * Never state or imply anything about Zoom, an acquisition, or what a vendor will do. Ask about change in general,
    and about the person's own past experience, so answers are evidence and not agreement with our hypothesis.
  * Open questions only, one at a time. Ask permission before recording or quoting.
  * This is research: no pitch in the call. Invitations disclose that the project's case company is Reo.Dev.
Everything here is plain text with {placeholders}: no model is involved, so output is predictable.
"""
import re

import personas

CORE = [
    ("1. Warm-up (3 min)", [
        "Could you walk me through your role and what your team at {company} is responsible for?",
        "How does {company} find and prioritise the developers and companies most likely to become customers? Who owns that?",
    ]),
    ("2. Current tools and who decides (6 min)", [
        "Which tools do you use today to understand developer or community activity (GitHub, docs, package downloads, community, product sign-ups)?",
        "Which one does your team rely on most? What would stop working if it disappeared tomorrow?",
        "How was it chosen, how long have you had it, and who signs off on renewing it?",
        "When does the contract or subscription come up for renewal, and how far ahead does the review start?",
    ]),
    ("3. Value and gaps (5 min)", [
        "What has it helped you do that you could not do before? Can you give a recent example?",
        "What do you still do by hand, or wish it did better?",
        "How do you show leadership that it is paying off? Which numbers do you use?",
    ]),
    ("4. Change and switching (6 min)", [
        "Think of the last time your team switched a go-to-market tool. What set it off?",
        "What has kept you from switching a tool you were unhappy with (contracts, integrations, workflows, procurement, time)?",
        "If a vendor you rely on changed its pricing, packaging, roadmap priorities or support, what would you do first? Who would you talk to?",
        "What would you need to see before you would even look at an alternative?",
    ]),
    ("5. What would make an evaluation worthwhile (4 min)", [
        "If you did evaluate a new tool, what would the process look like and how long would it take?",
        "What lowers the risk of switching for you: migration help, a pilot, onboarding support, a time-limited offer, reference customers, a comparison? Which matters most?",
        "How would a discount weigh against those things, compared with, say, a faster setup?",
    ]),
    ("6. How you like to be reached (3 min)", [
        "When a new tool catches your attention, where did you hear about it? What made you reply or ignore it?",
        "Who else would be involved in a decision like this (RevOps, security, finance, engineering, leadership)?",
    ]),
    ("7. Close (2 min)", [
        "Is there anything I did not ask that I should have?",
        "Who else, at your company or elsewhere, would give me a different view? Would you introduce me?",
        "May I quote you anonymously in our write-up? May I follow up with a couple of short questions?",
    ]),
]

# Extra questions depending on whether the person uses the tool the project is about.
USAGE = {
    "current": ["You use it today: what made you choose it over the alternatives you looked at?",
                "How deeply is it wired into your CRM, alerts and reporting? How hard would it be to replace?"],
    "past": ["You used it before: what made you stop? What replaced it?",
             "Looking back, what would have made you stay?"],
    "evaluated": ["You looked at it but did not buy: what decided it?"],
    "never": ["You have not used a dedicated tool for this: how do you cover the need today, and what has held you back?"],
}

PERSONA = {
    "DevRel / Community / Developer Marketing": [
        "Which developer signals do you trust (stars, docs visits, installs, community activity, event leads)? Which do you ignore?",
        "How do you connect community activity to pipeline or revenue today? What gets lost between community and sales?",
        "How do you share findings with sales: reports, alerts, meetings?",
    ],
    "Growth / Demand Gen / Marketing leader": [
        "How does a free sign-up or an open-source user become a paying customer at {company}? Where does it stall?",
        "Which signals decide which accounts get marketing or sales attention? Who built that scoring?",
        "How do you decide where the next marketing dollar goes? What do you need to see to move budget?",
    ],
    "RevOps / GTM Ops / Sales leader": [
        "How does product and community data reach your CRM today? Who maintains that pipeline?",
        "What would a migration involve for you (data, integrations, retraining, security review)? How long would it take?",
        "How do reps use the signals day to day, and how do you know they do?",
    ],
    "Founder / CEO": [
        "What are your top go-to-market priorities this year, and where does developer-led demand fit?",
        "How do you decide between building something in-house and buying a tool? What budget range is realistic?",
    ],
    "General": [
        "What does a typical week look like for you when it comes to finding and following up with developer-led leads?",
    ],
}

SIGNOFF = "Thank you again. I will send a short summary of what I learned if you would like one."


def persona_key(persona, title):
    """Map a lead's persona (or, for people found by name, their title) to one of PERSONA's keys."""
    if persona in PERSONA:
        return persona
    tier = personas.classify(title or "")
    return tier[1] if tier and tier[1] in PERSONA else "General"


def _fill(text, ctx):
    return re.sub(r"\{(\w+)\}", lambda m: ctx.get(m.group(1), m.group(0)), text)


def build_guide(lead, usage=None):
    """The interview plan for one lead. `usage` is one of USAGE's keys or None (ask it first)."""
    company = lead.get("company") or "your company"
    name = lead.get("contact_name") or ""
    pk = persona_key(lead.get("persona"), lead.get("contact_title"))
    ctx = {"company": company, "first_name": (name.split() or ["there"])[0]}
    sections = [(h, [_fill(q, ctx) for q in qs]) for h, qs in CORE]
    sections.insert(2, ("2b. Do they use the tool? (ask first, then pick the branch)", [
        "Have you used Common Room? (current user / used before / looked at it / never) Then use the matching branch below."]))
    branches = {k: [_fill(q, ctx) for q in v] for k, v in USAGE.items()}
    persona_qs = [_fill(q, ctx) for q in PERSONA[pk]]
    context = []
    if lead.get("company_type"):
        context.append(f"{company}: {lead['company_type'].replace('_', ' ')}" + (f", headcount {lead['headcount']}" if lead.get("headcount") else ""))
    if lead.get("devrel_evidence"):
        context.append("Public developer-community signal: " + lead["devrel_evidence"].split(" | ")[0][:300])
    if lead.get("how_identified"):
        context.append("How we found them: " + lead["how_identified"])
    if lead.get("persona") == personas.KNOWN_USER_PERSONA:
        context.append("They are quoted on Common Room's public customers page, so they very likely use it. Do not mention that page "
                       "unless they bring it up; ask the usage question as normal.")
    return {"name": name or "(no contact yet)", "title": lead.get("contact_title") or "", "company": company, "persona": pk,
            "context": context, "sections": sections, "branches": branches, "persona_questions": persona_qs, "signoff": SIGNOFF,
            "usage": usage}


def invite(lead, form_url, sender):
    """(subject, email_body, linkedin_message). Honest about who we are and that Reo.Dev is the case company."""
    name = lead.get("contact_name") or ""
    first = (name.split() or ["there"])[0]
    company = lead.get("company") or "your company"
    role = lead.get("contact_title")
    about = f"your experience as {role} at {company}" if role else f"your experience at {company}"
    subject = f"20-minute research chat about developer signals at {company}"
    body = (f"Hi {first},\n\n"
            "I am a student at Mesa School of Business working on a strategy project about how developer-focused companies "
            "find and act on developer activity. The case company is Reo.Dev. This is research, not a sales call, and I would "
            f"like to learn from {about}.\n\n"
            f"Would you be open to a 20-minute conversation? You can pick a time or say no here: {form_url}\n\n"
            f"Thank you,\n{sender}")
    linkedin = (f"Hi {first}, I am a Mesa School of Business student researching how developer-focused companies use developer "
                f"activity data (our case company is Reo.Dev). Could I ask you 20 minutes of questions about your work at {company}? "
                f"No pitch. Details and times: {form_url}")
    return subject, body, linkedin


def to_markdown(guide):
    lines = [f"# Interview guide: {guide['name']}", f"{guide['title']} at {guide['company']}  (persona: {guide['persona']})", ""]
    if guide["context"]:
        lines += ["## Context (internal)"] + [f"- {c}" for c in guide["context"]] + [""]
    for head, qs in guide["sections"]:
        lines += [f"## {head}"] + [f"{i}. {q}" for i, q in enumerate(qs, 1)] + [""]
    lines += ["## Branch by usage"] + [f"**{k}**\n" + "\n".join(f"- {q}" for q in v) for k, v in guide["branches"].items()] + [""]
    lines += [f"## For their role ({guide['persona']})"] + [f"- {q}" for q in guide["persona_questions"]] + ["", guide["signoff"]]
    return "\n".join(lines)
