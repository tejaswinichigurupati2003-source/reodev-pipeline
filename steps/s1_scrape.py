"""Step 1 - Scrape a company's own public pages (polite own scraper, robots.txt respected).

Output: data/<domain>/step1_raw.json  {"domain", "pages": {url: {"ok", "text", "error"?}}}
Pages: homepage + docs/developers, community, careers, pricing, about. Max 6 pages, 6000 chars each.
We also record two cheap signals from the raw HTML of ALL fetched pages: a GitHub org link and
community links (Discord, Slack, Discourse, forum), because those say "has a developer audience".
"""
import re

import pipeline_io as io
from scrapers import own_scraper

OUTPUT_FILE = "step1_raw.json"
PATHS = ["", "/docs", "/developers", "/community", "/careers", "/pricing", "/about"]
MAX_PAGES = 6
MAX_CHARS = 6000
COMMUNITY_HOSTS = ("discord.gg", "discord.com/invite", "slack.com", "discourse", "community.", "forum.", "reddit.com/r/")


def _signals(html_blobs):
    github, community = None, []
    for html in html_blobs:
        for url in re.findall(r'href="(https?://[^"]+)"', html):
            m = re.match(r"https://github\.com/([\w.-]+)/?$", url)
            if m and not github and m.group(1).lower() not in ("sponsors", "features", "about", "pricing"):
                github = url
            if any(h in url for h in COMMUNITY_HOSTS) and url not in community:
                community.append(url)
    return {"github_org": github, "community_links": community[:6]}


def run(domain, fresh=False, fetch=None):
    if io.exists(domain, OUTPUT_FILE) and not fresh:
        return io.load_json(domain, OUTPUT_FILE)
    fetch = fetch or own_scraper.fetch_doc
    pages, blobs = {}, []
    for path in PATHS:
        if len(pages) >= MAX_PAGES:
            break
        url = f"https://{domain}{path}"
        try:
            doc = fetch(url)
            pages[url] = {"ok": True, "text": doc["markdown"][:MAX_CHARS]}
            blobs.append(doc["raw_html"])
        except Exception as exc:  # 404 on /docs is normal: record and move on
            if path == "":
                pages[url] = {"ok": False, "text": "", "error": str(exc)}
    result = {"domain": domain, "pages": pages, **_signals(blobs)}
    io.save_json(domain, OUTPUT_FILE, result)
    return result
