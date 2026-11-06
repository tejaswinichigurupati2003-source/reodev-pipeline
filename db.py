"""Storage for the dashboard: Neon Postgres when DATABASE_URL is set, otherwise a local SQLite file (data/local.db).

    python sync_db.py        # push data/ (accounts, scores, contacts) to the database

Two tables:
  leads    one row per (account, contact). REPLACED on every sync (it is a copy of the pipeline's files).
  tracker  outreach status you type in the dashboard. NEVER touched by a sync, so your notes are safe.
SQL is written once with `?` placeholders; for Postgres they become `%s`.
"""
import os
import sqlite3
import threading
import time
from contextlib import contextmanager

from dotenv import load_dotenv

import export
import pipeline_io as io
import research_form

load_dotenv()

LEAD_COLUMNS = [c for c in export.COLUMNS if c not in ("contact_status", "last_touch", "channel", "reply", "interview_done", "notes")]
TRACKER_FIELDS = ["contact_status", "channel", "last_touch", "reply", "interview_done", "notes"]
STATUSES = ["not contacted", "queued", "contacted", "replied", "interview booked", "interview done", "not interested", "no reply"]
INT_COLUMNS = {"rank", "score", "fit", "value", "switching_proxy", "ease", "email_confidence"}


def configured():
    return bool(os.getenv("DATABASE_URL"))


_tls = threading.local()   # one connection and one set of timers per thread
_ready = set()             # databases whose tables we already checked in this process


class Conflict(Exception):
    """Someone else changed this record after the form was opened."""


def _pg_connection(url):
    """One long-lived Postgres connection per thread. A new connection costs several round trips (TLS, login), which is the
    biggest cost when the database is far away, so we keep it and only check it is still alive after it has been idle."""
    import psycopg
    from psycopg.rows import dict_row
    c = getattr(_tls, "conn", None)
    if c is not None and (c.closed or getattr(_tls, "url", None) != url):
        c = None
    if c is not None and time.monotonic() - getattr(_tls, "used", 0) > 15:  # idle a while: has the pooler dropped us?
        try:
            c.execute("SELECT 1")
            c.rollback()
        except Exception:
            try:
                c.close()
            except Exception:
                pass
            c = None
    if c is None:
        # prepare_threshold=None: Neon's pooler hands out a different server connection per transaction, so no server-side prepared statements
        c = psycopg.connect(url, row_factory=dict_row, connect_timeout=10, prepare_threshold=None)
        _tls.conn, _tls.url = c, url
    _tls.used = time.monotonic()
    return c


@contextmanager
def connect():
    url = os.getenv("DATABASE_URL")
    if url:
        conn, pg = _pg_connection(url), True
    else:
        path = io.DATA_DIR / "local.db"
        path.parent.mkdir(parents=True, exist_ok=True)
        conn, pg = sqlite3.connect(path), False
        conn.row_factory = sqlite3.Row
    try:
        yield _DB(conn, pg)
        conn.commit()
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        if pg:
            import psycopg
            if isinstance(exc, (psycopg.OperationalError, psycopg.InterfaceError)):  # a broken connection must not be reused
                try:
                    conn.close()
                except Exception:
                    pass
                _tls.conn = None
        raise
    finally:
        if not pg:
            conn.close()


def reset_timing():
    _tls.db_ms, _tls.db_n = 0.0, 0


def timing():
    """(milliseconds spent in the database, number of statements) since reset_timing()."""
    return getattr(_tls, "db_ms", 0.0), getattr(_tls, "db_n", 0)


class _DB:
    def __init__(self, conn, pg):
        self.conn, self.pg = conn, pg

    def execute(self, sql, params=()):
        t = time.perf_counter()
        cur = self.conn.cursor()
        cur.execute(sql.replace("?", "%s") if self.pg else sql, tuple(params))
        _tls.db_ms = getattr(_tls, "db_ms", 0.0) + (time.perf_counter() - t) * 1000
        _tls.db_n = getattr(_tls, "db_n", 0) + 1
        return cur

    def rows(self, sql, params=()):
        return [dict(r) for r in self.execute(sql, params).fetchall()]


def _ensure_column(db, table, column, ctype):
    """Add a column to an existing table if it is missing. Never touches existing rows."""
    if db.pg:
        db.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {ctype}")
    elif column not in [r["name"] for r in db.rows(f"PRAGMA table_info({table})")]:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ctype}")


def init(db, force=False):
    """Create the tables once per process (checking on every request costs a round trip per table)."""
    ident = "pg" if db.pg else str(io.DATA_DIR / "local.db")
    if ident in _ready and not force:
        return
    cols = ", ".join(f"{c} {'INTEGER' if c in INT_COLUMNS else 'TEXT'}" for c in LEAD_COLUMNS)
    db.execute(f"CREATE TABLE IF NOT EXISTS leads (key TEXT PRIMARY KEY, {cols})")
    db.execute("CREATE TABLE IF NOT EXISTS manual_leads (key TEXT PRIMARY KEY, company TEXT, domain TEXT, contact_name TEXT, "
               "contact_title TEXT, email TEXT, created_at TEXT)")
    db.execute("CREATE TABLE IF NOT EXISTS research_responses (id TEXT PRIMARY KEY, created_at TEXT, name TEXT, email TEXT, company TEXT, "
               "role TEXT, cr_status TEXT, tools TEXT, availability TEXT, consent TEXT, answers TEXT)")
    _ensure_column(db, "research_responses", "answers", "TEXT")  # tables created before the question form existed
    db.execute("CREATE TABLE IF NOT EXISTS lead_edits (key TEXT PRIMARY KEY, contact_name TEXT, contact_title TEXT, email TEXT, "
               "linkedin TEXT, edited TEXT, archived TEXT, updated_at TEXT)")
    db.execute("CREATE TABLE IF NOT EXISTS tracker (key TEXT PRIMARY KEY, contact_status TEXT, channel TEXT, last_touch TEXT, "
               "reply TEXT, interview_done TEXT, notes TEXT, updated_at TEXT)")
    for stmt in ("CREATE INDEX IF NOT EXISTS leads_tier ON leads (tier)", "CREATE INDEX IF NOT EXISTS leads_rank ON leads (rank)",
                 "CREATE INDEX IF NOT EXISTS tracker_stage ON tracker (contact_status)",
                 "CREATE INDEX IF NOT EXISTS manual_email ON manual_leads (email)",
                 "CREATE INDEX IF NOT EXISTS responses_created ON research_responses (created_at)",
                 "CREATE INDEX IF NOT EXISTS responses_email ON research_responses (email)"):
        db.execute(stmt)
    _ready.add(ident)


def lead_key(row):
    return f"{row['domain']}|{(row.get('email') or '').lower()}"


def sync(rows):
    """Replace the leads table with `rows` (from export.build_rows). Returns the number of rows."""
    with connect() as db:
        db.execute("DROP TABLE IF EXISTS leads")  # leads is only a copy of data/, so rebuilding it is safe (tracker is separate)
        init(db, force=True)
        for r in rows:
            vals = [lead_key(r)] + [r.get(c) for c in LEAD_COLUMNS]
            db.execute(f"INSERT INTO leads (key, {', '.join(LEAD_COLUMNS)}) VALUES ({', '.join('?' * (len(LEAD_COLUMNS) + 1))})", vals)
    return len(rows)


STAGE_ORDER = {name: n for n, name in enumerate(STATUSES)}
SORTS = {"rank": "Best match first", "score": "Highest score", "company": "Company A-Z", "stage": "Stage", "recent": "Recently changed"}
EMAIL_RE = r"[^@\s]+@[^@\s]+\.[^@\s]+"


HEAVY = ("draft_body", "devrel_evidence")  # long text only the profile page needs


def _decorate(rows, tracker, edits):
    """Add stage, notes, your edits and the archive flag to lead rows (synced and added-by-you alike)."""
    for r in rows:
        t = tracker.get(r["key"]) or {}
        r["contact_status"] = t.get("contact_status") if t.get("contact_status") in STATUSES else "not contacted"
        for f in ("channel", "last_touch", "reply", "interview_done", "notes", "updated_at"):
            r[f] = t.get(f)
        e = edits.get(r["key"]) or {}
        r["archived"] = e.get("archived") == "1"
        r["edited"] = e.get("edited") == "1"
        r["edit_updated_at"] = e.get("updated_at") or ""
        if r["edited"]:  # what you typed wins over what the pipeline found (and survives every sync)
            for f in ("contact_name", "contact_title", "email", "linkedin"):
                r[f] = e.get(f) or None
        elif e.get("linkedin"):
            r["linkedin"] = e["linkedin"]
    return rows


def _manual_row(m):
    return {**{c: None for c in LEAD_COLUMNS}, **m, "rank": 0, "tier": "MY", "account_status": "added by you",
            "how_identified": "Added manually", "manual": True, "linkedin": None}


def all_leads(db=None, light=True):
    """Every lead (synced + added by you) with tracker status, your edits and archive flag applied. One place, so every page
    agrees. light=True leaves out the two long text columns (drafts and evidence) that lists never show."""
    if db is None:
        with connect() as d:
            return all_leads(d, light)
    init(db)
    cols = "*" if not light else ", ".join(["key"] + [c for c in LEAD_COLUMNS if c not in HEAVY])
    tracker = {r["key"]: r for r in db.rows("SELECT * FROM tracker")}
    edits = {r["key"]: r for r in db.rows("SELECT * FROM lead_edits")}
    rows = [{**{h: None for h in HEAVY}, **l, "manual": False, "linkedin": None} for l in db.rows(f"SELECT {cols} FROM leads")]
    rows += [_manual_row(m) for m in db.rows("SELECT * FROM manual_leads")]
    return _decorate(rows, tracker, edits)


def get_lead(key):
    """One lead with everything (including drafts and evidence), using small keyed queries instead of loading all leads."""
    with connect() as db:
        init(db)
        found = db.rows("SELECT * FROM leads WHERE key = ?", [key])
        if found:
            base = {**found[0], "manual": False, "linkedin": None}
        else:
            m = db.rows("SELECT * FROM manual_leads WHERE key = ?", [key])
            if not m:
                return None
            base = _manual_row(m[0])
        tracker = {r["key"]: r for r in db.rows("SELECT * FROM tracker WHERE key = ?", [key])}
        edits = {r["key"]: r for r in db.rows("SELECT * FROM lead_edits WHERE key = ?", [key])}
    return _decorate([base], tracker, edits)[0]


def nav_counts():
    """Leads per tier for the sidebar, counted by the database (two small tables scanned, almost no data transferred)."""
    with connect() as db:
        init(db)
        rows = db.rows("SELECT l.tier AS tier, COUNT(*) AS n FROM leads l LEFT JOIN lead_edits e ON e.key = l.key "
                       "WHERE COALESCE(e.archived, '') <> '1' GROUP BY l.tier "
                       "UNION ALL SELECT 'MY' AS tier, COUNT(*) AS n FROM manual_leads m LEFT JOIN lead_edits e ON e.key = m.key "
                       "WHERE COALESCE(e.archived, '') <> '1'")
    counts = {}
    for r in rows:
        if r["n"]:
            counts[r["tier"]] = counts.get(r["tier"], 0) + r["n"]
    return counts


def filter_leads(rows, tier=None, search=None, only_email=False, status=None, archived=False, sort="rank"):
    """Filter and sort a list from all_leads(). archived: False = hide archived (default), True = only archived, "all" = both."""
    q = (search or "").strip().lower()
    out = []
    for r in rows:
        if archived is False and r["archived"]:
            continue
        if archived is True and not r["archived"]:
            continue
        if tier and r["tier"] != tier:
            continue
        if q and q not in " ".join(str(r.get(f) or "") for f in ("company", "domain", "contact_name", "contact_title", "email")).lower():
            continue
        if only_email and not r.get("email"):
            continue
        if status in STATUSES and r["contact_status"] != status:
            continue
        out.append(r)
    rank = lambda r: (0 if r["manual"] else 1, r["rank"] if r["rank"] is not None else 10 ** 6, (r["company"] or "").lower())
    if sort == "score":
        out.sort(key=lambda r: (r["score"] is None, -(r["score"] or 0), rank(r)))
    elif sort == "company":
        out.sort(key=lambda r: ((r["company"] or "").casefold(), r["contact_name"] or ""))
    elif sort == "stage":
        out.sort(key=lambda r: (STAGE_ORDER.get(r["contact_status"], 99), rank(r)))
    elif sort == "recent":
        out.sort(key=lambda r: r["updated_at"] or "", reverse=True)
    else:
        out.sort(key=rank)
    return out


def list_leads(*args, **kwargs):
    return filter_leads(all_leads(), *args, **kwargs)


def _exists(db, key):
    return bool(db.rows("SELECT key FROM leads WHERE key = ?", [key]) or db.rows("SELECT key FROM manual_leads WHERE key = ?", [key]))


def _email_in_use(db, email, exclude_key=None):
    """True if another lead already uses this address (case-insensitive)."""
    e = email.lower()
    for r in all_leads(db):
        if r["key"] != exclude_key and (r.get("email") or "").lower() == e:
            return True
    return False


def _claim(db, table, key, expected, now):
    """Compare-and-set on updated_at: succeeds only if nobody changed the record since the form was opened (expected = what the
    form carried; '' = it had no record yet). Atomic in the database, so two people saving at once cannot both win."""
    if expected is None:
        return
    if expected == "":
        cur = db.execute(f"INSERT INTO {table} (key, updated_at) VALUES (?, ?) ON CONFLICT (key) DO NOTHING", [key, now])
        if cur.rowcount != 1:
            cur = db.execute(f"UPDATE {table} SET updated_at = ? WHERE key = ? AND COALESCE(updated_at, '') = ''", [now, key])
    else:
        cur = db.execute(f"UPDATE {table} SET updated_at = ? WHERE key = ? AND updated_at = ?", [now, key, expected])
    if cur.rowcount != 1:
        raise Conflict("Someone changed this lead after you opened it, so nothing was saved. Reload the page to see their change.")


def save_lead_edit(key, fields, now, expected=None):
    """Edit a lead's contact details. Added-by-you leads are updated in place; synced leads keep your edit in a separate table so
    the next sync cannot overwrite it. Raises ValueError (safe to show) or KeyError (unknown lead)."""
    import re
    f = {k: (fields.get(k) or "").strip() for k in ("company", "domain", "contact_name", "contact_title", "email", "linkedin")}
    if f["email"] and not re.fullmatch(EMAIL_RE, f["email"]):
        raise ValueError("That does not look like an email address.")
    if f["linkedin"] and not f["linkedin"].lower().startswith(("http://", "https://")):
        raise ValueError("The LinkedIn link must start with http:// or https://")
    for k, n in (("company", 120), ("domain", 120), ("contact_name", 120), ("contact_title", 120), ("email", 200), ("linkedin", 300)):
        f[k] = f[k][:n]
    with connect() as db:
        init(db)
        if not _exists(db, key):
            raise KeyError(key)
        if f["email"] and _email_in_use(db, f["email"], key):
            raise ValueError("Another lead already uses that email address.")
        _claim(db, "lead_edits", key, expected, now)
        if key.startswith("manual:"):
            if not f["company"]:
                raise ValueError("Company is required.")
            db.execute("UPDATE manual_leads SET company = ?, domain = ?, contact_name = ?, contact_title = ?, email = ? WHERE key = ?",
                       [f["company"], f["domain"].lower(), f["contact_name"], f["contact_title"], f["email"], key])
            db.execute("INSERT INTO lead_edits (key, linkedin, updated_at) VALUES (?, ?, ?) "
                       "ON CONFLICT (key) DO UPDATE SET linkedin = excluded.linkedin, updated_at = excluded.updated_at",
                       [key, f["linkedin"], now])
        else:
            db.execute("INSERT INTO lead_edits (key, contact_name, contact_title, email, linkedin, edited, updated_at) "
                       "VALUES (?, ?, ?, ?, ?, '1', ?) ON CONFLICT (key) DO UPDATE SET contact_name = excluded.contact_name, "
                       "contact_title = excluded.contact_title, email = excluded.email, linkedin = excluded.linkedin, "
                       "edited = '1', updated_at = excluded.updated_at",
                       [key, f["contact_name"], f["contact_title"], f["email"], f["linkedin"], now])


def set_archived(key, flag, now):
    """Hide (or restore) a lead without deleting anything. Works for synced and added-by-you leads."""
    with connect() as db:
        init(db)
        if not _exists(db, key):
            raise KeyError(key)
        db.execute("INSERT INTO lead_edits (key, archived, updated_at) VALUES (?, ?, ?) "
                   "ON CONFLICT (key) DO UPDATE SET archived = excluded.archived, updated_at = excluded.updated_at",
                   [key, "1" if flag else "", now])


def add_manual(fields, now):
    """Add a contact you found yourself. Survives every sync. Returns its key."""
    import re
    import uuid
    company = (fields.get("company") or "").strip()[:120]
    if not company:
        raise ValueError("company is required")
    email = (fields.get("email") or "").strip()[:200]
    if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise ValueError("that does not look like an email address")
    key = "manual:" + uuid.uuid4().hex[:12]
    with connect() as db:
        init(db)
        if email and _email_in_use(db, email):
            raise ValueError("That email address is already on the board.")
        db.execute("INSERT INTO manual_leads (key, company, domain, contact_name, contact_title, email, created_at) VALUES (?,?,?,?,?,?,?)",
                   [key, company, (fields.get("domain") or "").strip().lower()[:120], (fields.get("name") or "").strip()[:120],
                    (fields.get("title") or "").strip()[:120], email, now])
    status = fields.get("status")
    if status in STATUSES and status != "not contacted":
        set_status(key, status, now)
    return key


def delete_manual(key):
    if not key.startswith("manual:"):
        raise KeyError(key)
    with connect() as db:
        init(db)
        db.execute("DELETE FROM manual_leads WHERE key = ?", [key])
        db.execute("DELETE FROM tracker WHERE key = ?", [key])
        db.execute("DELETE FROM lead_edits WHERE key = ?", [key])


def set_status(key, status, now):
    """Change only the stage of one lead (used by drag-and-drop). Other tracker fields are kept."""
    if status not in STATUSES:
        raise ValueError(status)
    with connect() as db:
        init(db)
        if not _exists(db, key):
            raise KeyError(key)
        db.execute("INSERT INTO tracker (key, contact_status, channel, last_touch, reply, interview_done, notes, updated_at) "
                   "VALUES (?, ?, '', '', '', '', '', ?) "
                   "ON CONFLICT (key) DO UPDATE SET contact_status = excluded.contact_status, updated_at = excluded.updated_at",
                   [key, status, now])


def update_tracker(key, fields, now, expected=None):
    """Save the typed-in tracker fields for one lead. Only known fields are written."""
    clean = {k: (fields.get(k) or "").strip()[:2000] for k in TRACKER_FIELDS}
    with connect() as db:
        init(db)
        if not _exists(db, key):
            raise KeyError(key)
        if clean["contact_status"] not in STATUSES:
            raise ValueError(f"Unknown stage: {clean['contact_status']!r}")
        _claim(db, "tracker", key, expected, now)
        cols = TRACKER_FIELDS + ["updated_at"]
        db.execute(f"INSERT INTO tracker (key, {', '.join(cols)}) VALUES ({', '.join('?' * (len(cols) + 1))}) "
                   f"ON CONFLICT (key) DO UPDATE SET {', '.join(f'{c} = excluded.{c}' for c in cols)}",
                   [key] + [clean[k] for k in TRACKER_FIELDS] + [now])


CR_STATUSES = ["I use it now", "I used it before", "I looked at it", "I have not used it"]
MAX_RESPONSES_PER_HOUR = 30  # a flood guard for the public form


def add_response(f, now):
    """Save one research-form answer. Raises ValueError with a message that is safe to show the visitor."""
    import re
    import uuid
    from datetime import datetime, timedelta
    clean = {k: (f.get(k) or "").strip() for k in ("name", "email", "company", "role", "cr_status", "tools", "availability")}
    if not clean["name"] or not clean["company"]:
        raise ValueError("Please fill in your name and company.")
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", clean["email"]):
        raise ValueError("Please enter a valid email address.")
    if f.get("consent") != "yes":
        raise ValueError("Please tick the box so we know you are happy to be contacted.")
    for k, n in (("name", 120), ("email", 200), ("company", 120), ("role", 120), ("tools", 500), ("availability", 500)):
        clean[k] = clean[k][:n]
    if clean["cr_status"] not in CR_STATUSES:
        clean["cr_status"] = ""
    with connect() as db:
        init(db)
        hour_ago = (datetime.fromisoformat(now) - timedelta(hours=1)).isoformat(timespec="seconds")
        if db.rows("SELECT COUNT(*) AS n FROM research_responses WHERE created_at > ?", [hour_ago])[0]["n"] >= MAX_RESPONSES_PER_HOUR:
            raise ValueError("We are getting a lot of replies right now. Please try again in a little while.")
        day_ago = (datetime.fromisoformat(now) - timedelta(days=1)).isoformat(timespec="seconds")
        if db.rows("SELECT id FROM research_responses WHERE LOWER(email) = ? AND created_at > ?", [clean["email"].lower(), day_ago]):
            return None  # same person sent it twice: say thanks, store once
        rid = uuid.uuid4().hex[:12]
        db.execute("INSERT INTO research_responses (id, created_at, name, email, company, role, cr_status, tools, availability, consent, answers) "
                   "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                   [rid, now, clean["name"], clean["email"], clean["company"], clean["role"], clean["cr_status"], clean["tools"],
                    clean["availability"], "yes", research_form.dumps(research_form.parse(f))])
    return rid


def list_responses():
    with connect() as db:
        init(db)
        return db.rows("SELECT * FROM research_responses ORDER BY created_at DESC")
