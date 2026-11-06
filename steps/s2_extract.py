"""Step 2 - Extract facts about the company from the scraped pages (LLM, temperature 0, then code checks).

Output: data/<domain>/step2_extracted.json
Code check: every devrel_signals quote must appear on the page it cites, otherwise the signal is dropped.
"""
import json
import re

import llm
import pipeline_io as io

OUTPUT_FILE = "step2_extracted.json"
COMPANY_TYPES = ["devtool", "open_source_company", "infrastructure_or_cloud", "data_or_ai_platform", "saas_other",
                 "non_software", "unknown"]
NULLABLE_BOOL = {"type": ["boolean", "null"]}
SCHEMA = {
    "type": "object",
    "properties": {
        "company": {"type": ["string", "null"]},
        "product_summary": {"type": ["string", "null"]},
        "company_type": {"type": "string", "enum": COMPANY_TYPES},
        "sells_to_developers": NULLABLE_BOOL,
        "is_open_source": NULLABLE_BOOL,
        "plg_signup": NULLABLE_BOOL,
        "headcount": {"type": ["string", "null"]},
        "use_case_for_intent": {"type": ["string", "null"]},
        "devrel_signals": {"type": "array", "items": {
            "type": "object",
            "properties": {"signal": {"type": "string"}, "evidence_quote": {"type": "string"}, "source_url": {"type": "string"}},
            "required": ["signal", "evidence_quote", "source_url"]}},
        "missing_info": {"type": "array", "items": {"type": "string"}},
        "reasoning": {"type": "string"},
    },
    "required": ["company_type", "sells_to_developers", "devrel_signals", "missing_info", "reasoning"],
}


def _norm(text):
    return re.sub(r"\s+", " ", text or "").strip().lower()


def render_pages(raw):
    return "\n\n".join(f"=== PAGE: {url} ===\n{p['text']}" for url, p in raw["pages"].items() if p.get("ok") and p.get("text"))


def verify_quotes(extracted, raw):
    """Drop devrel signals whose quote is not on the cited page. Returns (kept, dropped_count)."""
    kept, dropped = [], 0
    for s in extracted.get("devrel_signals") or []:
        page = raw["pages"].get(s.get("source_url"))
        if page and _norm(s.get("evidence_quote")) and _norm(s["evidence_quote"]) in _norm(page["text"]):
            kept.append(s)
        else:
            dropped += 1
    return kept, dropped


def run(domain, fresh=False):
    if io.exists(domain, OUTPUT_FILE) and not fresh:
        return io.load_json(domain, OUTPUT_FILE)
    raw = io.load_json(domain, "step1_raw.json")
    content = render_pages(raw)
    if not content:
        result = {"domain": domain, "company_type": "unknown", "sells_to_developers": None, "devrel_signals": [],
                  "missing_info": ["no page could be scraped"], "reasoning": "No readable pages."}
    else:
        values = {"domain": domain, "scraped_data": content}
        prompt = re.sub(r"\{(domain|scraped_data)\}", lambda m: values[m.group(1)], io.load_prompt("s2_extract.md"))
        result = {"domain": domain, **llm.generate_json(prompt, SCHEMA, temperature=0.0)}
        result["devrel_signals"], result["dropped_unverified_quotes"] = verify_quotes(result, raw)
    result["github_org"] = raw.get("github_org")
    result["community_links"] = raw.get("community_links", [])
    io.save_json(domain, OUTPUT_FILE, result)
    return result


if __name__ == "__main__":
    import sys

    print(json.dumps(run(sys.argv[1], fresh="--fresh" in sys.argv), indent=2))
