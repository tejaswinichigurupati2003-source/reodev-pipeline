"""Step 3 - Score the account (pure code, no LLM, so every point can be explained).

Four dimensions, weights in SCORING below (edit them; the numbers are ASSUMPTIONS, not measured data):
  fit        0-40  does it sell to developers, and what kind of company is it
  value      0-20  size proxy for deal value (headcount band; bigger = bigger deal)
  switching  0-25  signs of a live developer-growth motion that would want sharper intent data
  ease       0-15  can we reach them: account is publicly verified + a named contact exists (added after step 4)

Tier: A >= 65, B >= 45, C below, SKIP if clearly not a developer-facing software company.
IMPORTANT: "switching" is a propensity proxy. No public data says who is unhappy with Common Room,
so this must be validated in the interviews before anyone treats it as a forecast.
"""
import json

import pipeline_io as io

OUTPUT_FILE = "step3_score.json"
SCORING = {
    "fit": {"sells_to_developers_true": 20, "sells_to_developers_unknown": 5,
            "type_points": {"devtool": 10, "open_source_company": 10, "infrastructure_or_cloud": 10,
                            "data_or_ai_platform": 10, "saas_other": 3, "non_software": 0, "unknown": 2},
            "open_source": 5, "plg_signup": 5},
    "value": {"under_25": 5, "from_25_to_199": 12, "200_plus": 20, "unknown": 8},
    "switching": {"one_devrel_signal": 10, "three_plus_devrel_signals": 15, "community_link": 5, "github_org": 5},
    "ease": {"verified_public": 5, "contact_found": 10},
    "tiers": {"A": 65, "B": 45},
}


def _headcount_min(text):
    import re
    nums = [int(n.replace(",", "")) for n in re.findall(r"\d[\d,]*", text or "")]
    return min(nums) if nums else None


def score_account(extracted, status="unverified", contact_found=False):
    s = SCORING
    reasons = {}
    # fit
    fit = 0
    stdv = extracted.get("sells_to_developers")
    fit += s["fit"]["sells_to_developers_true"] if stdv else (s["fit"]["sells_to_developers_unknown"] if stdv is None else 0)
    fit += s["fit"]["type_points"].get(extracted.get("company_type") or "unknown", 2)
    fit += s["fit"]["open_source"] if extracted.get("is_open_source") else 0
    fit += s["fit"]["plg_signup"] if extracted.get("plg_signup") else 0
    reasons["fit"] = f"sells_to_developers={stdv}, type={extracted.get('company_type')}, oss={extracted.get('is_open_source')}, plg={extracted.get('plg_signup')}"
    # value
    hc = _headcount_min(extracted.get("headcount"))
    if hc is None:
        value, band = s["value"]["unknown"], "unknown"
    elif hc < 25:
        value, band = s["value"]["under_25"], "under 25"
    elif hc < 200:
        value, band = s["value"]["from_25_to_199"], "25-199"
    else:
        value, band = s["value"]["200_plus"], "200+"
    reasons["value"] = f"headcount band {band} (stated: {extracted.get('headcount')})"
    # switching propensity proxy
    n = len(extracted.get("devrel_signals") or [])
    switching = s["switching"]["three_plus_devrel_signals"] if n >= 3 else (s["switching"]["one_devrel_signal"] if n >= 1 else 0)
    switching += s["switching"]["community_link"] if extracted.get("community_links") else 0
    switching += s["switching"]["github_org"] if extracted.get("github_org") else 0
    reasons["switching"] = f"{n} verified devrel signals, community={bool(extracted.get('community_links'))}, github={bool(extracted.get('github_org'))}"
    # ease
    ease = (s["ease"]["verified_public"] if status == "verified_public" else 0) + (s["ease"]["contact_found"] if contact_found else 0)
    reasons["ease"] = f"status={status}, contact_found={contact_found}"

    total = fit + value + switching + ease
    skip = stdv is False or extracted.get("company_type") == "non_software"
    tier = "SKIP" if skip else ("A" if total >= s["tiers"]["A"] else "B" if total >= s["tiers"]["B"] else "C")
    return {"fit": fit, "value": value, "switching": switching, "ease": ease, "total": total, "tier": tier,
            "reasons": reasons, "note": "switching is a proxy to validate in interviews, not a forecast"}


def run(domain, status="unverified", contact_found=False, save=True):
    result = {"domain": domain, **score_account(io.load_json(domain, "step2_extracted.json"), status, contact_found)}
    if save:
        io.save_json(domain, OUTPUT_FILE, result)
    return result


if __name__ == "__main__":
    import sys

    print(json.dumps(run(sys.argv[1]), indent=2))
