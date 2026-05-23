"""Flask web dashboard for OptionsTrader."""

import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

# Ensure project root is on the path regardless of working directory
_WEB_DIR = Path(__file__).parent.resolve()
_PROJECT_ROOT = _WEB_DIR.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(_PROJECT_ROOT / ".env")

from flask import (
    Flask, flash, redirect, render_template, request, send_file,
    session, url_for,
)

try:
    import bcrypt
except ImportError:
    bcrypt = None

from options_trader.db import (
    get_candidates, get_positions, get_latest_snapshot, init_db,
    update_candidate_grade,
)
from options_trader import stats as _stats

app = Flask(__name__, template_folder="templates")
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret-change-me")
app.permanent_session_lifetime = timedelta(hours=24)

REPORTS_DIR = _PROJECT_ROOT / "reports" / "daily"


# ---------------------------------------------------------------------------
# Template helpers
# ---------------------------------------------------------------------------

@app.template_global()
def mmddyy(date_str: str) -> str:
    """'2026-06-18' or '2026-06-18T21:16' → '06/18/26'"""
    try:
        d = (date_str or "")[:10]
        return f"{d[5:7]}/{d[8:10]}/{d[2:4]}"
    except Exception:
        return date_str or "—"


@app.template_global()
def pretty_strategy(s: str) -> str:
    """'short_put_vertical' → 'Short Put Vertical'"""
    return (s or "").replace("_", " ").title()


@app.template_global()
def trade_instructions(legs: dict) -> list[str]:
    """Return two-line trade instructions derived from legs_json."""
    t = legs.get("type", "")
    if t == "iron_condor":
        return [
            f"Sell ${int(legs.get('short_put_strike', 0))}P / ${int(legs.get('short_call_strike', 0))}C",
            f"Buy  ${int(legs.get('long_put_strike',  0))}P / ${int(legs.get('long_call_strike',  0))}C",
        ]
    is_put  = "put"   in t
    is_long = "long_" in t
    opt = "Put" if is_put else "Call"
    ss  = int(legs.get("short_strike", 0))
    ls  = int(legs.get("long_strike",  0))
    if is_long:
        return [f"Buy  ${ls} {opt}", f"Sell ${ss} {opt}"]
    return [f"Sell ${ss} {opt}", f"Buy  ${ls} {opt}"]


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def _check_password(plain: str) -> bool:
    if bcrypt is None:
        return False
    stored_hash = os.getenv("DASHBOARD_PASSWORD_HASH", "")
    if not stored_hash:
        return False
    return bcrypt.checkpw(plain.encode(), stored_hash.encode())


@app.before_request
def require_login():
    if request.endpoint in ("login", "logout", "static", "apple_touch_icon"):
        return
    if not session.get("logged_in"):
        return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------

@app.route("/apple-touch-icon.png")
@app.route("/apple-touch-icon-precomposed.png")
def apple_touch_icon():
    return send_file(str(_PROJECT_ROOT / "web" / "static" / "optionsicon.png"), mimetype="image/png")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        password = request.form.get("password", "")
        if _check_password(password):
            session.permanent = True
            session["logged_in"] = True
            return redirect(url_for("dashboard"))
        flash("Invalid password.", "danger")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return redirect(url_for("dashboard"))


@app.route("/dashboard")
def dashboard():
    s = _stats.summary()
    breakdown = _stats.strategy_breakdown()
    today_str = date.today().isoformat()

    # Open positions with today's exit signals (real positions only)
    open_positions = get_positions(status="open", paper=0)
    signal_positions = []
    for pos in open_positions:
        snap = get_latest_snapshot(pos["id"])
        if snap and snap.get("date") == today_str and snap.get("exit_signal_triggered"):
            pos["signal_reason"] = snap.get("exit_signal_reason", "")
            signal_positions.append(pos)

    return render_template(
        "dashboard.html",
        summary=s,
        breakdown=breakdown,
        signal_positions=signal_positions,
    )


# ---------------------------------------------------------------------------
# Candidates
# ---------------------------------------------------------------------------

@app.route("/candidates")
def candidates():
    pending = get_candidates(status="pending")
    # Parse JSON fields for display
    for c in pending:
        try:
            c["legs"] = json.loads(c.get("legs_json") or "{}")
        except Exception:
            c["legs"] = {}
        try:
            c["greeks"] = json.loads(c.get("greeks_json") or "{}")
        except Exception:
            c["greeks"] = {}
    return render_template("candidates.html", candidates=pending)


@app.route("/candidates/<int:cid>/accept", methods=["POST"])
def accept_candidate(cid):
    try:
        from options_trader.inventory import accept_candidate as _accept
        pos_id = _accept(cid)
        flash(f"Candidate {cid} accepted → Position {pos_id} opened.", "success")
    except Exception as e:
        flash(f"Error accepting candidate {cid}: {e}", "danger")
    return redirect(url_for("candidates"))


@app.route("/candidates/<int:cid>/reject", methods=["POST"])
def reject_candidate(cid):
    reason = request.form.get("reason", "manual reject")
    try:
        from options_trader.inventory import reject_candidate as _reject
        _reject(cid, reason)
        flash(f"Candidate {cid} rejected.", "warning")
    except Exception as e:
        flash(f"Error rejecting candidate {cid}: {e}", "danger")
    return redirect(url_for("candidates"))


# ---------------------------------------------------------------------------
# Candidate rationale (Anthropic API)
# ---------------------------------------------------------------------------

@app.route("/candidates/<int:cid>/grade", methods=["POST"])
def set_candidate_grade(cid):
    data = request.get_json(silent=True) or {}
    grade = data.get("grade") or request.form.get("grade")
    if grade not in ("A", "B", "C", None, ""):
        return json.dumps({"error": "Invalid grade"}), 400, {"Content-Type": "application/json"}
    update_candidate_grade(cid, grade or None)
    return json.dumps({"ok": True, "grade": grade}), 200, {"Content-Type": "application/json"}


@app.route("/candidates/<int:cid>/rationale")
def candidate_rationale(cid):
    """Generate a Passarelli-based trade rationale via Claude."""
    from options_trader.db import get_conn
    from datetime import date as _date

    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return json.dumps({"error": "ANTHROPIC_API_KEY is not set in .env"}), 500, \
               {"Content-Type": "application/json"}

    with get_conn() as cx:
        row = cx.execute("SELECT * FROM candidates WHERE id=?", (cid,)).fetchone()
    if row is None:
        return json.dumps({"error": "Candidate not found"}), 404, \
               {"Content-Type": "application/json"}

    c     = dict(row)
    legs  = json.loads(c.get("legs_json")   or "{}")
    greeks = json.loads(c.get("greeks_json") or "{}")
    t     = legs.get("type", c.get("strategy", ""))
    width = legs.get("width") or 1
    exp   = legs.get("expiration", "")
    dte   = ((_date.fromisoformat(exp) - _date.today()).days) if exp else "?"

    # Spot price (best-effort)
    spot_str = "unavailable"
    try:
        from options_trader.polygon_client import get_underlying_price
        spot_str = f"${get_underlying_price(c['ticker']):.2f}"
    except Exception:
        pass

    # Build leg description
    if t == "iron_condor":
        leg_desc = (
            f"Short {legs.get('short_put_strike')}P / {legs.get('short_call_strike')}C, "
            f"Long {legs.get('long_put_strike')}P / {legs.get('long_call_strike')}C"
        )
        short_delta_str = (
            f"put Δ {greeks.get('short_put_delta', 0):.3f}, "
            f"call Δ {greeks.get('short_call_delta', 0):.3f}"
        )
    else:
        leg_desc = f"Short {legs.get('short_strike')}, Long {legs.get('long_strike')}"
        short_delta_str = f"{greeks.get('short_delta', 0):.3f}"

    strategy_label = pretty_strategy(c.get("strategy", t))
    cr_w = c['credit'] / width if width else 0

    prompt = f"""You are an options trading analyst applying Dan Passarelli's framework from "Trading Options Greeks."

A live screening system generated this candidate. Provide a focused, practical rationale for the trade — no disclaimers, no generic boilerplate.

TRADE DETAILS
─────────────
Underlying : {c['ticker']}  (spot {spot_str})
Strategy   : {strategy_label}
Legs       : {leg_desc}
Expiration : {exp}  ({dte} DTE)
Net Credit : ${c['credit']:.2f}  ({cr_w:.1%} of ${width} width)
IV (short) : {(c.get('iv') or 0):.1%}
PoP        : {(c.get('pop') or 0):.1%}
Short Δ    : {short_delta_str}
Net Δ      : {greeks.get('net_delta', 0):+.4f}
Net Θ      : {greeks.get('net_theta', 0):+.4f}
Net V      : {greeks.get('net_vega',  0):+.4f}

Answer each section below. Be specific to this ticker and setup — not generic.

## 1. Why This Strategy Right Now
Why was {strategy_label} selected for {c['ticker']} at this moment? Reference the IV environment, where price sits relative to support/resistance or the 52-week range, and what condition triggered this strategy over alternatives.

## 2. Passarelli Framework Analysis
Assess this setup using Passarelli's framework: Is IV elevated or compressed — and what does that mean for premium selling vs. buying here? What does the short delta of {short_delta_str} imply about the probability target and OTM cushion? Describe the theta profile over the {dte}-day hold and whether it's collecting decay efficiently.

## 3. What Makes This Trade Succeed / What Invalidates It
Give specific price levels, IV moves, or time thresholds that define success vs. failure.

## 4. Key Risks to Monitor
Name the 1–2 most critical Greeks or market events to watch as this position ages toward {exp}."""

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=900,
            messages=[{"role": "user", "content": prompt}],
        )
        return json.dumps({"rationale": msg.content[0].text}), 200, \
               {"Content-Type": "application/json"}
    except Exception as e:
        return json.dumps({"error": str(e)}), 500, \
               {"Content-Type": "application/json"}


# ---------------------------------------------------------------------------
# Positions
# ---------------------------------------------------------------------------

@app.route("/positions")
def positions():
    tab = request.args.get("tab", "open")

    if tab == "paper":
        pos_list = get_positions(paper=1)
    else:
        status = "open" if tab == "open" else "closed"
        pos_list = get_positions(status=status, paper=0)

    # Attach latest snapshot to open/paper positions
    if tab in ("open", "paper"):
        for pos in pos_list:
            snap = get_latest_snapshot(pos["id"])
            pos["snapshot"] = snap
            if snap:
                try:
                    pos["current_greeks"] = json.loads(snap.get("current_greeks_json") or "{}")
                except Exception:
                    pos["current_greeks"] = {}

    # Parse legs_json for display
    for pos in pos_list:
        try:
            pos["legs"] = json.loads(pos.get("legs_json") or "{}")
        except Exception:
            pos["legs"] = {}
        try:
            pos["entry_greeks"] = json.loads(pos.get("entry_greeks_json") or "{}")
        except Exception:
            pos["entry_greeks"] = {}

    return render_template("positions.html", positions=pos_list, tab=tab)


@app.route("/positions/<int:pid>/detail")
def position_detail(pid):
    """Return entry vs current snapshot data for the ticker popup."""
    from options_trader.db import get_conn
    with get_conn() as cx:
        row = cx.execute("SELECT * FROM positions WHERE id=?", (pid,)).fetchone()
    if row is None:
        return json.dumps({"error": "Not found"}), 404, {"Content-Type": "application/json"}
    pos = dict(row)
    snap = get_latest_snapshot(pid)
    entry_greeks = json.loads(pos.get("entry_greeks_json") or "{}")
    current_greeks = {}
    if snap and snap.get("current_greeks_json"):
        try:
            current_greeks = json.loads(snap["current_greeks_json"])
        except Exception:
            pass

    entry_iv = pos.get("entry_iv") or entry_greeks.get("short_iv")
    cost_to_close = None
    if snap and snap.get("unrealized_pnl") is not None:
        cost_to_close = round(pos["entry_credit"] - snap["unrealized_pnl"], 4)

    return json.dumps({
        "entry": {
            "date": pos.get("entry_date"),
            "underlying": pos.get("entry_price_underlying"),
            "iv": entry_iv,
            "option_price": pos.get("entry_credit"),
        },
        "current": {
            "date": snap["date"] if snap else None,
            "underlying": snap.get("current_price_underlying") if snap else None,
            "iv": current_greeks.get("short_iv"),
            "option_price": cost_to_close,
        },
    }), 200, {"Content-Type": "application/json"}


@app.route("/positions/<int:pid>/close", methods=["POST"])
def close_position(pid):
    exit_debit_str = request.form.get("exit_debit", "")
    reason = request.form.get("reason", "manual close")
    try:
        exit_debit = float(exit_debit_str)
        from options_trader.inventory import close_position as _close
        _close(pid, exit_debit, reason)
        flash(f"Position {pid} closed.", "success")
    except ValueError:
        flash("Invalid exit debit value.", "danger")
    except Exception as e:
        flash(f"Error closing position {pid}: {e}", "danger")
    return redirect(url_for("positions"))


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------

@app.route("/reports")
def reports():
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_files = sorted(REPORTS_DIR.glob("*.html"), reverse=True)
    report_list = []
    for f in report_files:
        stat = f.stat()
        report_list.append({
            "date": f.stem,
            "filename": f.name,
            "size_kb": round(stat.st_size / 1024, 1),
        })
    return render_template("reports.html", reports=report_list)


@app.route("/reports/<date_str>")
def view_report(date_str):
    # Sanitize — only allow date-like names
    safe_name = date_str.replace("/", "").replace("..", "")
    report_path = REPORTS_DIR / f"{safe_name}.html"
    if not report_path.exists():
        flash(f"Report not found: {safe_name}", "danger")
        return redirect(url_for("reports"))
    return send_file(str(report_path), mimetype="text/html")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5001, debug=False)
