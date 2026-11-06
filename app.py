"""Lead dashboard (Flask). Runs locally (`python app.py`) and on Vercel (it exports `app`).

Reads leads from Neon (DATABASE_URL). You can open a lead, edit its contact details, change its stage, keep notes, add contacts of
your own and archive leads. It cannot send email, run the pipeline, or change scores.

Access: set DASHBOARD_PASSWORD to require a sign-in, or DASHBOARD_PUBLIC=1 to open the dashboard to anyone with the link (they can
then view AND edit). With neither it refuses to serve. The research form is always public; its answers need RESPONSES_KEY.
"""
import hashlib
import hmac
import os
import secrets
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

from dotenv import load_dotenv
from flask import (Flask, abort, flash, g, get_flashed_messages, jsonify, redirect, render_template, request, session,
                   url_for)
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

import db
import interview
import research_form

load_dotenv()

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)  # behind Vercel: build correct https links
PASSWORD = os.getenv("DASHBOARD_PASSWORD") or ""
PUBLIC = os.getenv("DASHBOARD_PUBLIC") == "1"
app.secret_key = os.getenv("SECRET_KEY") or hashlib.sha256(("dash:" + PASSWORD).encode()).hexdigest()
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_SECURE=bool(os.getenv("VERCEL")))
PAGE_SIZE = 25
BOARD_CAP = 30   # cards drawn per column; the column header always shows the true count
TIERS = ("A", "B", "C", "SKIP", "MY")
PUBLIC_ENDPOINTS = ("login", "static", "research", "research_thanks", "favicon")


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")  # also used as a version stamp, so it must not repeat


@app.before_request
def start_timer():
    g.t0 = time.perf_counter()
    db.reset_timing()


@app.after_request
def headers(resp):
    resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    resp.headers["Cache-Control"] = "no-store"  # lead data must never be shown stale
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Vary"] = "X-Requested-With"
    ms, n = db.timing()
    resp.headers["Server-Timing"] = f"db;dur={ms:.1f};desc=\"{n} queries\", app;dur={(time.perf_counter() - getattr(g, 't0', time.perf_counter())) * 1000:.1f}"
    return resp


@app.before_request
def gate():
    if PUBLIC:  # explicitly opened by the owner: no sign-in at all
        return None
    if not PASSWORD:
        return "Dashboard disabled: set DASHBOARD_PASSWORD (or DASHBOARD_PUBLIC=1) in the environment.", 503
    if request.endpoint in PUBLIC_ENDPOINTS:
        return None
    if not session.get("ok"):
        return redirect(url_for("login"))
    return None


def _csrf():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return session["csrf"]


app.jinja_env.globals["csrf"] = _csrf


def _check_csrf():
    if not hmac.compare_digest(request.form.get("csrf", ""), session.get("csrf", "x")):
        abort(400, "Your page was out of date. Please reload it and try again.")


def _everyone():
    """All leads (without the long text columns), loaded at most once per request."""
    if not hasattr(g, "everyone"):
        g.everyone = db.all_leads()
    return g.everyone


def _nav():
    """Leads per tier for the sidebar. Called by the sidebar itself, so pages without one (the public research form, login)
    never pay for it. A database hiccup must never break a page."""
    try:
        if hasattr(g, "everyone"):  # the page already loaded every lead: count in memory
            counts = {}
            for l in g.everyone:
                if not l["archived"]:
                    counts[l["tier"]] = counts.get(l["tier"], 0) + 1
            return counts
        if not hasattr(g, "nav_counts"):  # a page that does not need the leads: let the database count them, once
            g.nav_counts = db.nav_counts()
        return g.nav_counts
    except Exception:
        return {}


app.jinja_env.globals["nav"] = _nav
app.jinja_env.globals["flashes"] = lambda: get_flashed_messages(with_categories=True)


@app.context_processor
def basics():
    return {"public": PUBLIC}


@app.template_filter("safe_url")
def safe_url(value):
    """Only http(s) links are clickable: a seed CSV could hold javascript: links."""
    return value if str(value or "").lower().startswith(("http://", "https://")) else ""


def _wants_json():
    return request.headers.get("X-Requested-With") == "fetch" or request.path == "/tracker/status"


def _back(default="board"):
    """Where to return after a form: the page the form was on, but only if it is on this site."""
    target = request.form.get("next") or request.referrer or ""
    p = urlparse(target)
    if p.netloc and p.netloc != request.host:
        return url_for(default)
    path = (p.path + ("?" + p.query if p.query else "")) if (p.path.startswith("/") and not p.path.startswith("//")) else ""
    return path or url_for(default)


@app.errorhandler(HTTPException)
def http_error(e):
    msg = e.description if e.code in (400, 403, 404, 405, 409) else "Something went wrong on our side."
    if _wants_json():
        return jsonify({"ok": False, "error": msg}), e.code
    return render_template("error.html", code=e.code, title=e.name, message=msg), e.code


@app.errorhandler(Exception)
def crash(e):
    app.logger.exception("unhandled error")
    if _wants_json():
        return jsonify({"ok": False, "error": "Something went wrong on our side. Please try again."}), 500
    return render_template("error.html", code=500, title="Something went wrong",
                           message="We could not load that page. Please try again in a moment."), 500


@app.route("/favicon.ico")
def favicon():
    return "", 204


# ---------------------------------------------------------------- sign in
@app.route("/login", methods=["GET", "POST"])
def login():
    if PUBLIC:
        return redirect(url_for("board"))
    error = None
    if request.method == "POST":
        if PASSWORD and hmac.compare_digest(request.form.get("password", "").encode(), PASSWORD.encode()):
            session.clear()
            session["ok"] = True
            return redirect(url_for("board"))
        error = "Wrong password."
    return render_template("login.html", error=error)


@app.post("/logout")
def logout():
    _check_csrf()
    session.clear()
    return redirect(url_for("login"))


# ---------------------------------------------------------------- pages
@app.route("/")
def home():
    return redirect(url_for("board"))


def _filters():
    """The filters in the address bar, validated. Returns (dict for filtering, dict of non-empty values for building links)."""
    tier = request.args.get("tier") if request.args.get("tier") in TIERS else None
    status = request.args.get("status") if request.args.get("status") in db.STATUSES else None
    sort = request.args.get("sort") if request.args.get("sort") in db.SORTS else "rank"
    search = (request.args.get("q") or "").strip()[:80]
    only_email = request.args.get("email") == "1"
    archived = request.args.get("archived") == "1"
    f = dict(tier=tier, search=search or None, only_email=only_email, status=status, archived=archived, sort=sort)
    keep = {k: v for k, v in {"tier": tier, "status": status, "q": search or None, "email": "1" if only_email else None,
                              "archived": "1" if archived else None, "sort": sort if sort != "rank" else None}.items() if v}
    return f, keep


@app.route("/list")
def index():
    f, keep = _filters()
    live = [l for l in _everyone() if not l["archived"]]
    rows = db.filter_leads(_everyone(), **f)
    pages = max(1, -(-len(rows) // PAGE_SIZE))
    try:
        page = min(max(int(request.args.get("page", 1)), 1), pages)
    except ValueError:
        page = 1
    counts = {}
    for l in live:
        counts[l["tier"]] = counts.get(l["tier"], 0) + 1
    stage_counts = {s: sum(1 for l in live if l["contact_status"] == s) for s in db.STATUSES}
    stats = {"rows": len(live), "with_email": sum(1 for l in live if l.get("email")),
             "contacted": stage_counts["contacted"], "replied": stage_counts["replied"]}
    template = "_results.html" if request.headers.get("X-Requested-With") == "fetch" else "index.html"
    return render_template(template, leads=rows[(page - 1) * PAGE_SIZE: page * PAGE_SIZE], total=len(rows), page=page, pages=pages,
                           keep=keep, base={k: v for k, v in keep.items() if k != "tier"}, tier=f["tier"], q=f["search"] or "", counts=counts, statuses=db.STATUSES, only_email=f["only_email"],
                           status=f["status"], sort=f["sort"], sorts=db.SORTS, archived=f["archived"], stats=stats,
                           stage_counts=stage_counts, archived_count=sum(1 for l in _everyone() if l["archived"]))


@app.route("/board")
def board():
    f, keep = _filters()
    f["status"] = None  # the board shows every stage; a stage filter makes no sense here
    f["sort"] = "rank"
    leads = db.filter_leads(_everyone(), **f)
    now = datetime.now(timezone.utc)
    cols = {s: [] for s in db.STATUSES}
    for l in leads:
        try:
            l["days"] = (now - datetime.fromisoformat(l["updated_at"])).days if l.get("updated_at") else None
        except ValueError:
            l["days"] = None
        cols[l["contact_status"]].append(l)
    if request.headers.get("X-Requested-With") == "fetch":
        resp = app.make_response(render_template("_columns.html", cols=cols, statuses=db.STATUSES, tier=f["tier"], only_email=f["only_email"],
                                                 q=f["search"] or "", cap=BOARD_CAP, total=len(leads)))
        resp.headers["X-Total-Count"] = str(len(leads))
        return resp
    return render_template("board.html", cols=cols, statuses=db.STATUSES, tier=f["tier"], only_email=f["only_email"], q=f["search"] or "",
                           archived=f["archived"], total=len(leads), cap=BOARD_CAP)


@app.route("/lead")
def lead():
    key = request.args.get("key", "")
    l = db.get_lead(key)
    if not l:
        abort(404, "We could not find that lead. It may have been removed.")
    return render_template("lead.html", l=l, statuses=db.STATUSES)


# ---------------------------------------------------------------- changes
@app.post("/lead/edit")
def lead_edit():
    _check_csrf()
    key = request.form.get("key", "")
    try:
        db.save_lead_edit(key, request.form, now_iso(), expected=request.form.get("v"))
        flash("Contact details saved.", "ok")
    except (ValueError, db.Conflict) as exc:
        flash(str(exc), "error")
    except KeyError:
        abort(404, "We could not find that lead.")
    return redirect(url_for("lead", key=key))


@app.post("/lead/archive")
def lead_archive():
    _check_csrf()
    key, flag = request.form.get("key", ""), request.form.get("archive") == "1"
    try:
        db.set_archived(key, flag, now_iso())
    except KeyError:
        abort(404, "We could not find that lead.")
    flash("Lead archived. You can restore it from the list view." if flag else "Lead restored.", "ok")
    return redirect(url_for("board") if flag else url_for("lead", key=key))


@app.post("/contacts/add")
def contacts_add():
    """Add a contact you found yourself (it stays across syncs). Answers JSON to the board's form, a redirect otherwise."""
    _check_csrf()
    try:
        key = db.add_manual(request.form, now_iso())
    except ValueError as exc:
        if _wants_json():
            return jsonify({"ok": False, "error": str(exc)}), 400
        flash(str(exc), "error")
        return redirect(url_for("board"))
    flash("Contact added.", "ok")
    if _wants_json():
        return jsonify({"ok": True, "key": key})
    return redirect(url_for("board"))


@app.post("/contacts/delete")
def contacts_delete():
    _check_csrf()
    try:
        db.delete_manual(request.form.get("key", ""))
    except KeyError:
        abort(404, "Only contacts you added yourself can be deleted. Synced leads can be archived instead.")
    flash("Contact removed.", "ok")
    return redirect(url_for("board"))


@app.post("/tracker/status")
def tracker_status():
    """JSON endpoint for drag-and-drop and the stage menus: {key, status}. Changes only the stage, never the notes."""
    if not hmac.compare_digest(request.headers.get("X-CSRF", ""), session.get("csrf", "x")):
        abort(400, "Your page was out of date. Please reload it and try again.")
    data = request.get_json(silent=True) or {}
    try:
        db.set_status(str(data.get("key", "")), str(data.get("status", "")), now_iso())
    except KeyError:
        abort(404, "We could not find that lead. Reload the page.")
    except ValueError:
        abort(400, "That is not a valid stage.")
    return {"ok": True}


@app.post("/tracker")
def tracker():
    """Save stage, channel, last touch, reply, interview and notes for one lead."""
    _check_csrf()
    try:
        db.update_tracker(request.form.get("key", ""), request.form, now_iso(), expected=request.form.get("v"))
        flash("Saved.", "ok")
    except (ValueError, db.Conflict) as exc:
        flash(str(exc), "error")
    except KeyError:
        abort(404, "We could not find that lead.")
    return redirect(_back("index"))


# ---------------------------------------------------------------- interview + research
def _find_lead(key):
    l = db.get_lead(key)
    if not l:
        abort(404, "We could not find that lead.")
    return l


@app.route("/interview")
def interview_home():
    return render_template("interview.html", core=interview.CORE, usage=interview.USAGE, persona=interview.PERSONA)


@app.route("/interview/guide")
def interview_guide():
    lead = _find_lead(request.args.get("key", ""))
    guide = interview.build_guide(lead)
    sender = os.getenv("SENDER_NAME") or "[Your name]"
    subject, body, linkedin = interview.invite(lead, url_for("research", _external=True), sender)
    if request.args.get("format") == "md":
        name = "".join(ch for ch in guide["name"] if ch.isalnum()) or "guide"
        return app.response_class(interview.to_markdown(guide), mimetype="text/markdown",
                                  headers={"Content-Disposition": f"attachment; filename=interview-{name}.md"})
    return render_template("guide.html", g=guide, lead=lead, subject=subject, body=body, linkedin=linkedin)


def _research_context(form, error=None, thanks=False):
    return dict(form=form, error=error, thanks=thanks, questions=research_form.QUESTIONS, profile=research_form.PROFILE,
                schedule=research_form.SCHEDULE, cr_statuses=db.CR_STATUSES, refs=research_form.REFERRALS, opt_out=research_form.OPT_OUT,
                contact=os.getenv("RESEARCH_CONTACT_EMAIL") or "")


@app.route("/research", methods=["GET", "POST"])
def research():
    """Public, interactive form we send to the people we want to interview. No sign-in. Works without JavaScript too."""
    from werkzeug.datastructures import ImmutableMultiDict
    if request.method == "GET":
        return render_template("research.html", **_research_context(ImmutableMultiDict()))
    form, error = request.form, None
    if form.get("website"):  # hidden field: only bots fill it. Pretend it worked.
        return jsonify({"ok": True}) if _wants_json() else redirect(url_for("research_thanks"))
    try:
        db.add_response(form, now_iso())
        return jsonify({"ok": True}) if _wants_json() else redirect(url_for("research_thanks"))
    except ValueError as exc:
        error = str(exc)
    if _wants_json():
        return jsonify({"ok": False, "error": error}), 400
    return render_template("research.html", **_research_context(form, error)), 400


@app.route("/research/thanks")
def research_thanks():
    from werkzeug.datastructures import ImmutableMultiDict
    return render_template("research.html", **_research_context(ImmutableMultiDict(), thanks=True))


def _responses_allowed():
    """Respondents' details are personal data: even in public mode they need the secret RESPONSES_KEY."""
    key = os.getenv("RESPONSES_KEY") or ""
    if not key:
        return False
    if session.get("resp_ok"):
        return True
    if hmac.compare_digest(request.args.get("key", "").encode(), key.encode()):
        session["resp_ok"] = True
        return True
    return False


@app.route("/responses")
def responses():
    if not os.getenv("RESPONSES_KEY"):
        abort(404, "The responses page is not set up. Add RESPONSES_KEY to the environment to turn it on.")
    if not _responses_allowed():  # configured, but no (or a wrong) key: ask for it instead of a dead end
        return render_template("responses_locked.html", wrong=bool(request.args.get("key"))), (403 if request.args.get("key") else 200)
    if request.args.get("key"):
        return redirect(url_for("responses"))  # drop the key from the address bar
    rows = db.list_responses()
    for r in rows:
        parsed = research_form.loads(r.get("answers"))
        r["qa"], r["profile"], r["quote_ok"] = research_form.qa_list(parsed), research_form.profile_list(parsed), bool(parsed.get("quote_ok"))
    return render_template("responses.html", rows=rows)


def _csv(rows, cols, filename):
    import csv
    import io as _io

    import export as _export
    buf = _io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow({c: _export._safe(r.get(c)) for c in cols})
    return app.response_class("﻿" + buf.getvalue(), mimetype="text/csv",
                              headers={"Content-Disposition": f"attachment; filename={filename}"})


@app.route("/responses.csv")
def responses_csv():
    if not _responses_allowed():
        abort(404)
    rows = db.list_responses()
    for r in rows:
        r.update(research_form.flatten(research_form.loads(r.get("answers"))))
    cols = (["created_at", "name", "email", "company", "role", "cr_status", "availability"]
            + [f"p_{g['name']}" for g in research_form.FIELD_GROUPS] + [q["id"] for q in research_form.QUESTIONS] + ["quote_ok"])
    return _csv(rows, cols, "research-responses.csv")


@app.route("/export.csv")
def export_csv():
    """Download what is currently filtered, as CSV (text that a spreadsheet could run as a formula is neutralised)."""
    import export as _export
    f, _ = _filters()
    return _csv(db.filter_leads(db.all_leads(light=False), **f), list(_export.COLUMNS) + ["archived"], "reodev-leads.csv")


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000)
