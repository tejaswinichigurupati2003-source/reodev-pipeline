"""Who counts as which persona, from a job title. Pure code with no imports from the rest of the project,
so both the contact finder and the dashboard (which is deployed on its own) can use it."""
import re

KNOWN_USER_PERSONA = "Common Room user (named on their customers page)"
FOUNDER_TIER = 4

TIERS = [
    (1, "DevRel / Community / Developer Marketing", re.compile(
        r"developer (?:relations|advocacy|advocate|marketing|experience|community|programs?|growth)|\bdevrel\b|\bdevex\b|"
        r"\bcommunity\b|developer evangelist|open source (?:program|lead|manager)|\bdx\b")),
    (2, "Growth / Demand Gen / Marketing leader", re.compile(
        r"\b(?:head|director|vp|vice president|chief|lead)\b.*\b(?:growth|demand|marketing|cmo|acquisition)\b|\bcmo\b|"
        r"\b(?:growth|demand gen\w*|product marketing)\b.*\b(?:head|director|vp|lead|manager)\b")),
    (3, "RevOps / GTM Ops / Sales leader", re.compile(
        r"\brevops\b|revenue operations|\bgtm\b|go[- ]to[- ]market|sales operations|\bsales ops\b|"
        r"\b(?:head|director|vp|vice president)\b.*\bsales\b|\bcro\b|chief revenue officer")),
    (4, "Founder / CEO", re.compile(r"\bco-?founder\b|\bfounder\b|\bceo\b|chief executive")),
]


def classify(position):
    """Job title -> (tier, label), or None if the title is not on the persona list."""
    title = (position or "").lower()
    if not title:
        return None
    for tier, label, pattern in TIERS:
        if pattern.search(title):
            return tier, label
    return None
