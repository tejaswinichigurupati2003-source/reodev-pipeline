"""Storage for the dashboard: Neon Postgres when DATABASE_URL is set, otherwise a local SQLite file (data/local.db).

    python sync_db.py        # push data/ (accounts, scores, contacts) to the database

Two tables:
  leads    one row per (account, contact). REPLACED on every sync (it is a copy of the pipeline's files).
  tracker  outreach status you type in the dashboard. NEVER touched by a sync, so your notes are safe.
SQL is written once with `?` placeholders; for Postgres they become `%s`.
"""
import os
import sqlite3
from contextlib import contextmanager

from dotenv import load_dotenv

import export
import pipeline_io as io

load_dotenv()

LEAD_COLUMNS = [c for c in export.COLUMNS if c not in ("contact_status", "last_touch", "channel", "reply", "interview_done", "notes")]
TRACKER_FIELDS = ["contact_status", "channel", "last_touch", "reply", "interview_done", "notes"]
STATUSES = ["not contacted", "queued", "contacted", "replied", "interview booked", "interview done", "not interested", "no reply"]
INT_COLUMNS = {"rank", "score", "fit", "value", "switching_proxy", "ease", "email_confidence"}


def configured():
    return bool(os.getenv("DATABASE_URL"))


@contextmanager
def connect():
    url = os.getenv("DATABASE_URL")
    if url:
        import psycopg
        from psycopg.rows import dict_row
        conn, pg = psycopg.connect(url, row_factory=dict_row), True
    else:
        path = io.DATA_DIR / "local.db"
        path.parent.mkdir(parents=True, exist_ok=True)
        conn, pg = sqlite3.connect(path), False
        conn.row_factory = sqlite3.Row
    try:
        yield _DB(conn, pg)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


class _DB:
    def __init__(self, conn, pg):
        self.conn, self.pg = conn, pg

    def execute(self, sql, params=()):
        cur = self.conn.cursor()
        cur.execute(sql.replace("?", "%s") if self.pg else sql, tuple(params))
        return cur

    def rows(self, sql, params=()):
        return [dict(r) for r in self.execute(sql, params).fetchall()]


def init(db):
    cols = ", ".join(f"{c} {'INTEGER' if c in INT_COLUMNS else 'TEXT'}" for c in LEAD_COLUMNS)
    db.execute(f"CREATE TABLE IF NOT EXISTS leads (key TEXT PRIMARY KEY, {cols})")
    db.execute("CREATE TABLE IF NOT EXISTS manual_leads (key TEXT PRIMARY KEY, company TEXT, domain TEXT, contact_name TEXT, "
               "contact_title TEXT, email TEXT, created_at TEXT)")
    db.execute("CREATE TABLE IF NOT EXISTS research_responses (id TEXT PRIMARY KEY, created_at TEXT, name TEXT, email TEXT, company TEXT, "
               "role TEXT, cr_status TEXT, tools TEXT, availability TEXT, consent TEXT)")
    db.execute("CREATE TABLE IF NOT EXISTS tracker (key TEXT PRIMARY KEY, contact_status TEXT, channel TEXT, last_touch TEXT, "
               "reply TEXT, interview_done TEXT, notes TEXT, updated_at TEXT)")


def lead_key(row):
    return f"{row['domain']}|{(row.get('email') or '').lower()}"


def sync(rows):
    """Replace the leads table with `rows` (from export.build_rows). Returns the number of rows."""
    with connect() as db:
        db.execute("DROP TABLE IF EXISTS leads")  # leads is only a copy of data/, so rebuilding it is safe (tracker is separate)
        init(db)
        for r in rows:
            vals = [lead_key(r)] + [r.get(c) for c in LEAD_COLUMNS]
            db.execute(f"INSERT INTO leads (key, {', '.join(LEAD_COLUMNS)}) VALUES ({', '.join('?' * (len(LEAD_COLUMNS) + 1))})", vals)
    return len(rows)


def list_leads(tier=None, search=None, only_email=False, status=None):
    with connect() as db:
        init(db)
        sql = ("SELECT l.*, COALESCE(t.contact_status, 'not contacted') AS contact_status, t.channel, t.last_touch, "
               "t.reply, t.interview_done, t.notes, t.updated_at FROM leads l LEFT JOIN tracker t ON t.key = l.key")
        where, params = [], []
        if tier and tier != "MY":
            where.append("l.tier = ?")
            params.append(tier)
        if tier == "MY":
            where.append("1 = 0")
        if search:
            where.append("(LOWER(l.company) LIKE ? OR LOWER(l.domain) LIKE ? OR LOWER(COALESCE(l.contact_name, '')) LIKE ?)")
            params += [f"%{search.lower()}%"] * 3
        if only_email:
            where.append("COALESCE(l.email, '') <> ''")
        if status in STATUSES:
            where.append("COALESCE(t.contact_status, 'not contacted') = ?")
            params.append(status)
        if where:
            sql += " WHERE " + " AND ".join(where)
        rows = db.rows(sql + " ORDER BY l.rank", params)
        manual = []
        if tier in (None, "MY"):
            for m in db.rows("SELECT m.*, COALESCE(t.contact_status, 'not contacted') AS contact_status, t.channel, t.last_touch, "
                             "t.reply, t.interview_done, t.notes, t.updated_at FROM manual_leads m LEFT JOIN tracker t ON t.key = m.key "
                             "ORDER BY m.created_at DESC"):
                q = (search or "").lower()
                if q and q not in f"{m['company']} {m['domain']} {m['contact_name']}".lower():
                    continue
                if only_email and not m["email"]:
                    continue
                if status in STATUSES and m["contact_status"] != status:
                    continue
                manual.append({**{c: None for c in LEAD_COLUMNS}, **m, "rank": 0, "tier": "MY", "account_status": "added by you",
                               "how_identified": "Added manually", "manual": True})
        return manual + rows


def _exists(db, key):
    return bool(db.rows("SELECT key FROM leads WHERE key = ?", [key]) or db.rows("SELECT key FROM manual_leads WHERE key = ?", [key]))


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


def update_tracker(key, fields, now):
    """Save the typed-in tracker fields for one lead. Only known fields are written."""
    clean = {k: (fields.get(k) or "").strip()[:2000] for k in TRACKER_FIELDS}
    if clean["contact_status"] not in STATUSES:
        clean["contact_status"] = "not contacted"
    with connect() as db:
        init(db)
        if not _exists(db, key):
            raise KeyError(key)
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
        db.execute("INSERT INTO research_responses (id, created_at, name, email, company, role, cr_status, tools, availability, consent) "
                   "VALUES (?,?,?,?,?,?,?,?,?,?)",
                   [rid, now, clean["name"], clean["email"], clean["company"], clean["role"], clean["cr_status"], clean["tools"],
                    clean["availability"], "yes"])
    return rid


def list_responses():
    with connect() as db:
        init(db)
        return db.rows("SELECT * FROM research_responses ORDER BY created_at DESC")
