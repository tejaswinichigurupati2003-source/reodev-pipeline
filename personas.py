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


# ---------------------------------------------------------------- who to interview
# Research interviews need people who use developer-signal tools every day AND are likely to answer a stranger. That is
# DevRel, community, developer marketing and growth. Senior sales leaders, recruiters, event and performance marketers
# and executives at big companies are busy, rarely the day-to-day user, and unlikely to reply.
_AVOID = [
    (re.compile(r"recruit|talent|\bhr\b|human resources|people (?:ops|operations|partner|team)|people & culture"), "Hiring or HR role, not a day-to-day user"),
    (re.compile(r"event|field marketing|trade ?show|conference"), "Event marketing, not a day-to-day user"),
    (re.compile(r"performance marketing|paid (?:media|social|search|acquisition)|\bppc\b|\bsem\b|media buy|\bbrand\b|public relations|\bpr\b|communications"), "Performance, paid or brand marketing, not a day-to-day user"),
    (re.compile(r"\b(?:director|vp|vice president|head|chief|svp|evp|general manager)\b.*\b(?:sales|business development|revenue officer|bd)\b|\bcro\b|chief revenue|\bsales (?:director|vp|head)\b"), "Senior sales leader: busy and rarely the daily user"),
    (re.compile(r"legal|counsel|finance|\bcfo\b|account(?:ing|ant)|controller|procurement"), "Finance or legal role, not a day-to-day user"),
    (re.compile(r"employee resource|\berg\b|\bdei\b|diversity|inclusion|inorganic|corporate development|\bm&a\b|paid growth"),
     "Internal, corporate or paid-acquisition role, not a developer-community user"),
    (re.compile(r"social media|commercial|enterprise (?:growth|sales|account)|regional (?:director|manager|vp|head|lead)|account (?:executive|manager|director)|territory|inside sales"),
     "Sales, regional or social-media role, not a developer-community user"),
]
_EXEC = re.compile(r"\bco-?founder\b|\bfounder\b|\bceo\b|chief executive|\bcto\b|\bcoo\b|\bpresident\b|chief (?:technology|operating|product|marketing) officer|\bcmo\b|\bcpo\b")
_BEST = re.compile(r"developer (?:relations|advocacy|advocate|marketing|experience|community|programs?|growth|education|evangelist)|\bdevrel\b|\bdevex\b|\bdx\b|"
                   r"community (?:manager|lead|head|program|director|engineer|marketing)|head of community|director of community|open[- ]source (?:program|community|lead|manager|outreach)|"
                   r"\bcommunity\b|technical (?:evangelist|advocate)|developer success")
_STRONG = re.compile(r"\bgrowth\b|product[- ]led|\bplg\b|developer (?:acquisition|adoption)")
_OPS = re.compile(r"marketing (?:ops|operations)|revenue operations|\brevops\b|\bgtm\b|go[- ]to[- ]market|sales (?:ops|operations)|business operations|"
                  r"operations manager|pipeline (?:ops|operations|generation)|\bautomation\b|\bsignals?\b|\babm\b|demand gen|product marketing|lifecycle|marketing manager|marketing lead")
LABELS = {4: "Best fit", 3: "Strong fit", 2: "Possible", 1: "Low fit", 0: "Unlikely"}


def interview_fit(title, headcount=None):
    """How good a research-interview target is this person? Returns {"score": 0..4, "label", "reason"}; score -1 means no contact yet.
    Pure rules on the job title (and company size for founders), so the reason can always be explained."""
    t = (title or "").lower().strip()
    if not t:
        return {"score": 1, "label": LABELS[1], "reason": "No job title, so the role is unknown"}
    for pattern, reason in _AVOID:
        if pattern.search(t):
            return {"score": 0, "label": LABELS[0], "reason": reason}
    if _EXEC.search(t):
        nums = [int(n.replace(",", "")) for n in re.findall(r"\d[\d,]*", headcount or "")]
        if nums and min(nums) < 25:
            return {"score": 1, "label": LABELS[1], "reason": "Founder of a very small company: hands-on, but busy"}
        return {"score": 0, "label": LABELS[0], "reason": "Executive or founder at a larger company: rarely the daily user, unlikely to reply"}
    if _BEST.search(t):
        return {"score": 4, "label": LABELS[4], "reason": "Works with developers and the community every day"}
    if _STRONG.search(t):
        return {"score": 3, "label": LABELS[3], "reason": "Growth role: owns the developer-led funnel"}
    if _OPS.search(t):
        return {"score": 2, "label": LABELS[2], "reason": "Runs or uses the tooling day to day"}
    return {"score": 1, "label": LABELS[1], "reason": "Role is not clearly connected to developer signals"}
