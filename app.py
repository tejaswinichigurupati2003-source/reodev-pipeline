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
    if not PASSWORD:
        return "Dashboard disabled: set DASHBOARD_PASSWORD in the environment.", 503
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
    if tier not in (None, "A", "B", "C", "SKIP"):
        tier = None
    search = (request.args.get("q") or "").strip()[:80] or None
    leads = db.list_leads(tier, search)
    counts = {}
    for l in db.list_leads():
        counts[l["tier"]] = counts.get(l["tier"], 0) + 1
    return render_template("index.html", leads=leads, tier=tier, q=search or "", counts=counts, statuses=db.STATUSES,
                           with_email=sum(1 for l in leads if l.get("email")))


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
