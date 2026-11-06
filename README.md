# Reo.Dev switch-campaign lead pipeline

Built on the same pattern as the Sprinto pipeline: **find accounts -> scrape -> extract facts -> score -> find contacts -> export**.
It prepares leads only. It never sends anything.

```
discover.py        0. Common Room customer list -> data/accounts.csv   (+ your own seeds)
steps/s1_scrape    1. polite scrape of the company's own pages (robots.txt respected)
steps/s2_extract   2. LLM extracts facts; every quote is checked against the page, fake quotes are dropped
steps/s3_score     3. code-only score: fit 40 + value 20 + switching proxy 25 + ease 15 -> tier A/B/C/SKIP
steps/s4_contacts  4. Hunter.io people -> up to 3 per account by persona (DevRel > Growth/Marketing > RevOps/Sales > Founder if <25 staff)
export.py          5. data/leads.csv: ranked tracker, empty columns for outreach and interview status
```

## Setup
```bash
cp .env.example .env     # then fill in the keys
uv run --python 3.12 --with-requirements requirements.txt pytest      # offline tests, no keys needed
```
Keys: `OPENROUTER_API_KEY` + `OPENROUTER_MODEL` (step 2), `APOLLO_API_KEY` and/or `HUNTER_API_KEY` (step 4), `DATABASE_URL` (Neon). Scraping needs none.
Tests need pytest too: add `--with pytest` to the test command.

## Run
```bash
uv run --python 3.12 --with-requirements requirements.txt python discover.py
uv run --python 3.12 --with-requirements requirements.txt python discover.py --seed my_list.csv --no-scrape
uv run --python 3.12 --with-requirements requirements.txt python pipeline.py --no-contacts   # free: scrape + score all
uv run --python 3.12 --with-requirements requirements.txt python pipeline.py --max-hunter 40 # then spend Hunter credits on the top-ranked
```
Seed CSV columns: `name,domain,source_url,how_identified` (anything you found by hand: G2, LinkedIn posts, interviews).

## Contact providers (Apollo and/or Hunter)
`CONTACT_PROVIDER=auto` (default) tries **Apollo first, Hunter as the fallback**; `apollo` or `hunter` forces one. Set at least one key.
* Apollo's people search is free but returns **no emails**; emails come from a second enrichment call that costs credits
  (docs: 1 credit per person with an email). So we search, pick the best 3 by title in code, and enrich only those.
* A free Apollo account may be refused API access (403): the pipeline then falls back to Hunter by itself.
* Each provider's answer is cached forever per domain (`data/<domain>/apollo_raw.json`, `hunter_raw.json`).
* Check Apollo's current free credit allowance in your account; I could not confirm it from the docs.

## Hosting: Neon + Vercel dashboard
1. Create a Neon project, copy the **pooled** connection string into `DATABASE_URL` in `.env`.
2. Set `DASHBOARD_PASSWORD` and a long random `SECRET_KEY` in `.env`.
3. Run the pipeline as usual; with `DATABASE_URL` set it syncs to Neon at the end (or run `python sync_db.py` any time).
4. Local preview: `python app.py` then open http://127.0.0.1:5000 (without `DATABASE_URL` it uses a local SQLite file).
5. Vercel: import the GitHub repo, add `DATABASE_URL`, `DASHBOARD_PASSWORD`, `SECRET_KEY` as environment variables, deploy.
   Vercel only serves the dashboard. **The pipeline itself runs on your computer** (scraping is too slow for serverless).

The dashboard shows ranked accounts and contacts and lets you edit the outreach tracker (status, channel, notes).
It is password protected, refuses to start without a password, uses CSRF tokens, escapes all text, and cannot send email.
A re-sync replaces the `leads` table but never touches your tracker notes.

## Honest limits (say these out loud in the deck)
* **Who is a Common Room customer is not public in full.** `verified_public` = Common Room's own site names them. Everything else is `unverified`, and rows with no resolvable website are `needs_domain`. Nothing is guessed.
* **The switching score is an assumption.** No public data says who is unhappy. It is a proxy (live developer-growth motion); validate it in interviews. Weights live in `steps/s3_score.py` (`SCORING`).
* **Hunter free plan is ~50 searches/month, 10 people per domain.** `--max-hunter` caps spend, accounts are searched best-score-first, and a domain is never searched twice (cached in `data/<domain>/hunter_raw.json`). For 200 accounts, expect to fill the rest with the LinkedIn search link in `leads.csv` and manual finds. Hunter's own confidence is shown; treat emails as unverified until a send bounces or passes a verifier.
* LinkedIn is never scraped (link only). Check outreach against GDPR/CAN-SPAM and each tool's terms before sending.
* The scraper only reads public pages; JS-only sites may come back thin (flagged as missing info, not guessed).
