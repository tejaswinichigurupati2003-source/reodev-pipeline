"""Lead dashboard (Flask). Runs locally (`python app.py`) and on Vercel (it exports `app`).

Reads the `leads` table from Neon (DATABASE_URL) and lets you update the outreach tracker.
It holds personal data, so it is PASSWORD PROTECTED and refuses to serve at all if DASHBOARD_PASSWORD is not set.
It only shows and edits leads. It cannot send email, run the pipeline, or change scores.
"""
import hashlib
import hmac
import os
import secrets
from datetime import datetime, timezone

from dotenv import load_dotenv
from flask import Flask, abort, redirect, render_template, request, session, url_for

import db

load_dotenv()

app = Flask(__name__)
PASSWORD = os.getenv("DASHBOARD_PASSWORD") or ""
PUBLIC = os.getenv("DASHBOARD_PUBLIC") == "1"  # opt-in: anyone with the link can view AND edit the tracker
app.secret_key = os.getenv("SECRET_KEY") or hashlib.sha256(("dash:" + PASSWORD).encode()).hexdigest()
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_SECURE=bool(os.getenv("VERCEL")))


@app.after_request
def headers(resp):
    resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Frame-Options"] = "DENY"
    return resp


@app.before_request
def gate():
    if PUBLIC:  # explicitly opened by the owner: no sign-in at all
        return None
    if not PASSWORD:
        return "Dashboard disabled: set DASHBOARD_PASSWORD (or DASHBOARD_PUBLIC=1) in the environment.", 503
    if request.endpoint in ("login", "static"):
        return None
    if not session.get("ok"):
        return redirect(url_for("login"))
    return None


def _csrf():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return session["csrf"]


app.jinja_env.globals["csrf"] = _csrf


@app.template_filter("safe_url")
def safe_url(value):
    """Only http(s) links are clickable: a seed CSV could hold javascript: links."""
    return value if str(value or "").lower().startswith(("http://", "https://")) else ""


def _check_csrf():
    if not hmac.compare_digest(request.form.get("csrf", ""), session.get("csrf", "x")):
        abort(400)


@app.route("/login", methods=["GET", "POST"])
def login():
    if PUBLIC:
        return redirect(url_for("index"))
    error = None
    if request.method == "POST":
        if PASSWORD and hmac.compare_digest(request.form.get("password", "").encode(), PASSWORD.encode()):
            session.clear()
            session["ok"] = True
            return redirect(url_for("index"))
        error = "Wrong password."
    return render_template("login.html", error=error)


@app.post("/logout")
def logout():
    _check_csrf()
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
def index():
    tier = request.args.get("tier") or None
    if tier not in (None, "A", "B", "C", "SKIP", "MY"):
        tier = None
    search = (request.args.get("q") or "").strip()[:80] or None
    only_email = request.args.get("email") == "1"
    status = request.args.get("status") if request.args.get("status") in db.STATUSES else None
    leads = db.list_leads(tier, search, only_email, status)
    everyone = db.list_leads()
    counts = {}
    for l in everyone:
        counts[l["tier"]] = counts.get(l["tier"], 0) + 1
    stats = {"rows": len(everyone), "with_email": sum(1 for l in everyone if l.get("email")),
             "contacted": sum(1 for l in everyone if l["contact_status"] not in ("not contacted", "queued")),
             "replied": sum(1 for l in everyone if l["contact_status"] in ("replied", "interview booked", "interview done"))}
    return render_template("index.html", public=PUBLIC, leads=leads, tier=tier, q=search or "", counts=counts, statuses=db.STATUSES,
                           only_email=only_email, status=status, stats=stats)


@app.route("/board")
def board():
    tier = request.args.get("tier") if request.args.get("tier") in ("A", "B", "C", "SKIP", "MY") else None
    only_email = request.args.get("email") == "1"
    leads = db.list_leads(tier, None, only_email)
    now = datetime.now(timezone.utc)
    cols = {s: [] for s in db.STATUSES}
    for l in leads:
        try:
            l["days"] = (now - datetime.fromisoformat(l["updated_at"])).days if l.get("updated_at") else None
        except ValueError:
            l["days"] = None
        cols[l["contact_status"] if l["contact_status"] in cols else "not contacted"].append(l)
    counts = {}
    for l in db.list_leads():
        counts[l["tier"]] = counts.get(l["tier"], 0) + 1
    return render_template("board.html", public=PUBLIC, cols=cols, statuses=db.STATUSES, tier=tier, only_email=only_email,
                           total=len(leads), counts=counts, total_all=sum(counts.values()))


@app.post("/contacts/add")
def contacts_add():
    """Add a contact you found yourself (it stays across syncs)."""
    _check_csrf()
    try:
        db.add_manual(request.form, datetime.now(timezone.utc).isoformat(timespec="seconds"))
    except ValueError as exc:
        return f"Could not add the contact: {exc}", 400
    return redirect(url_for("board"))


@app.post("/contacts/delete")
def contacts_delete():
    _check_csrf()
    try:
        db.delete_manual(request.form.get("key", ""))
    except KeyError:
        abort(404)
    return redirect(url_for("board"))


@app.post("/tracker/status")
def tracker_status():
    """JSON endpoint for drag-and-drop: {key, status}. CSRF token comes in the X-CSRF header."""
    if not hmac.compare_digest(request.headers.get("X-CSRF", ""), session.get("csrf", "x")):
        abort(400)
    data = request.get_json(silent=True) or {}
    try:
        db.set_status(str(data.get("key", "")), str(data.get("status", "")), datetime.now(timezone.utc).isoformat(timespec="seconds"))
    except KeyError:
        abort(404)
    except ValueError:
        abort(400)
    return {"ok": True}


@app.route("/export.csv")
def export_csv():
    """Download what is currently filtered, as CSV (text that a spreadsheet could run as a formula is neutralised)."""
    import csv
    import io as _io

    import export as _export
    tier = request.args.get("tier") if request.args.get("tier") in ("A", "B", "C", "SKIP") else None
    rows = db.list_leads(tier, (request.args.get("q") or "").strip()[:80] or None, request.args.get("email") == "1",
                         request.args.get("status"))
    cols = [c for c in _export.COLUMNS]
    buf = _io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow({c: _export._safe(r.get(c)) for c in cols})
    return app.response_class("﻿" + buf.getvalue(), mimetype="text/csv",
                              headers={"Content-Disposition": "attachment; filename=reodev-leads.csv"})


@app.post("/tracker")
def tracker():
    _check_csrf()
    try:
        db.update_tracker(request.form.get("key", ""), request.form, datetime.now(timezone.utc).isoformat(timespec="seconds"))
    except KeyError:
        abort(404)
    return redirect(request.referrer or url_for("index"))


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000)
