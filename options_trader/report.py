"""HTML report generator for daily options trader summary."""

from datetime import datetime
from typing import Optional

from options_trader import stats as _stats
from options_trader.db import get_candidates, get_positions, get_latest_snapshot


def generate_daily_report(reprice_results: list[dict]) -> str:
    """Build a full self-contained HTML report for today.

    Sections:
        1. Header: date, time, portfolio summary stats
        2. Exit Signals: positions where exit_signal_triggered today
        3. Repricing Summary: table of all repriced positions
        4. Pending Candidates: current pending candidates table
        5. Performance: 30/60/90d P&L, win rate, strategy breakdown

    Returns an HTML string (Bootstrap 5 via CDN, no external CSS files).
    """
    now = datetime.now()
    date_str = now.strftime("%A, %B %d, %Y")
    time_str = now.strftime("%H:%M:%S")

    s = _stats.summary()
    pending_candidates = get_candidates(status="pending")
    strat_breakdown = _stats.strategy_breakdown()
    pnl_by_und = _stats.pnl_by_underlying()

    # Positions with exit signals in today's reprice
    exit_signal_positions = [r for r in reprice_results if r.get("exit_signal_triggered")]

    # Open positions for repricing table
    open_positions = get_positions(status="open")
    pos_map = {p["id"]: p for p in open_positions}

    html = f"""<!DOCTYPE html>
<html lang="en" data-bs-theme="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>OptionsTrader Daily Report — {date_str}</title>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css">
    <style>
        body {{ font-family: 'Segoe UI', system-ui, sans-serif; }}
        .metric-card {{ text-align: center; }}
        .metric-value {{ font-size: 2rem; font-weight: 700; }}
        .positive {{ color: #28a745; }}
        .negative {{ color: #dc3545; }}
        .neutral {{ color: #6c757d; }}
        .badge-stop {{ background-color: #dc3545; }}
        .badge-target {{ background-color: #28a745; }}
        .badge-dte {{ background-color: #ffc107; color: #000; }}
        .badge-delta {{ background-color: #fd7e14; }}
        table {{ font-size: 0.875rem; }}
        .section-title {{ border-bottom: 2px solid #6c757d; padding-bottom: 0.5rem; margin-bottom: 1.5rem; }}
    </style>
</head>
<body>
<div class="container-fluid py-4">

    <!-- Header -->
    <div class="row mb-4">
        <div class="col">
            <h1 class="display-5 fw-bold">OptionsTrader Daily Report</h1>
            <p class="text-muted">{date_str} &bull; Generated at {time_str}</p>
        </div>
    </div>

    <!-- Section 1: Portfolio Summary Metrics -->
    <h2 class="section-title">Portfolio Summary</h2>
    <div class="row g-3 mb-5">
        {_metric_card("Open Positions", s['open_positions'], "")}
        {_metric_card("Pending Candidates", s['pending_candidates'], "")}
        {_metric_card("Win Rate (All)", f"{s['win_rate_all']:.1%}", _sign_class(s['win_rate_all'] - 0.5))}
        {_metric_card("Win Rate (90d)", f"{s['win_rate_90d']:.1%}", _sign_class(s['win_rate_90d'] - 0.5))}
        {_metric_card("Portfolio Delta", f"{s['portfolio_delta']:+.3f}", _sign_class(s['portfolio_delta']))}
        {_metric_card("Closed Trades", s['total_closed_trades'], "")}
    </div>

    <!-- P&L Row -->
    <div class="row g-3 mb-5">
        {_metric_card("P&L 30d", f"${s['pnl_30d'] * 100:+.2f}", _sign_class(s['pnl_30d']))}
        {_metric_card("P&L 60d", f"${s['pnl_60d'] * 100:+.2f}", _sign_class(s['pnl_60d']))}
        {_metric_card("P&L 90d", f"${s['pnl_90d'] * 100:+.2f}", _sign_class(s['pnl_90d']))}
        {_metric_card("Avg P&L/Trade", f"${s['avg_pnl_per_trade'] * 100:+.2f}", _sign_class(s['avg_pnl_per_trade']))}
        {_metric_card("Exit Signals Active", s['exit_signals_active'], "negative" if s['exit_signals_active'] > 0 else "")}
    </div>

    <!-- Section 2: Exit Signals -->
    <h2 class="section-title">Exit Signals</h2>
    {_exit_signals_section(exit_signal_positions, pos_map)}

    <!-- Section 3: Repricing Summary -->
    <h2 class="section-title">Repricing Summary</h2>
    {_reprice_table(reprice_results, pos_map)}

    <!-- Section 4: Pending Candidates -->
    <h2 class="section-title">Pending Candidates</h2>
    {_candidates_table(pending_candidates)}

    <!-- Section 5: Performance -->
    <h2 class="section-title">Performance</h2>

    <h4>Strategy Breakdown</h4>
    {_strategy_breakdown_table(strat_breakdown)}

    <h4 class="mt-4">P&L by Underlying</h4>
    {_pnl_by_underlying_table(pnl_by_und)}

</div>
<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>
</body>
</html>"""
    return html


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------

def _metric_card(label: str, value, css_class: str) -> str:
    val_str = str(value)
    color_cls = f" {css_class}" if css_class else ""
    return f"""
        <div class="col-md-2 col-sm-4 col-6">
            <div class="card h-100 metric-card">
                <div class="card-body">
                    <div class="metric-value{color_cls}">{val_str}</div>
                    <div class="text-muted small">{label}</div>
                </div>
            </div>
        </div>"""


def _sign_class(val) -> str:
    if val > 0:
        return "positive"
    elif val < 0:
        return "negative"
    return "neutral"


def _exit_signals_section(signals: list[dict], pos_map: dict) -> str:
    if not signals:
        return '<div class="alert alert-success">No exit signals triggered today.</div>'

    rows = ""
    for r in signals:
        pos = pos_map.get(r.get("position_id", -1), {})
        ticker = r.get("ticker", pos.get("ticker", "—"))
        reason = r.get("exit_signal_reason", "")
        badges = _signal_badges(reason)
        unrl = r.get("unrealized_pnl")
        cost = r.get("cost_to_close")
        entry_credit = pos.get("entry_credit", 0)

        rows += f"""<tr>
            <td><strong>{pos.get('id', '—')}</strong></td>
            <td>{ticker}</td>
            <td>{pos.get('strategy', '—')}</td>
            <td>{pos.get('entry_date', '—')}</td>
            <td>${entry_credit:.2f}</td>
            <td>{"$" + f"{cost:.2f}" if cost is not None else "—"}</td>
            <td class="{_sign_class(unrl or 0)}">{"$" + f"{(unrl or 0) * 100:+.2f}" if unrl is not None else "—"}</td>
            <td>{badges}</td>
        </tr>"""

    return f"""<div class="table-responsive">
    <table class="table table-striped table-hover">
        <thead><tr>
            <th>ID</th><th>Ticker</th><th>Strategy</th><th>Entry Date</th>
            <th>Entry Credit</th><th>Cost to Close</th><th>Unrl P&L (1 lot)</th><th>Signals</th>
        </tr></thead>
        <tbody>{rows}</tbody>
    </table></div>"""


def _signal_badges(reason: str) -> str:
    if not reason:
        return ""
    badges = []
    r = reason.lower()
    if "50%" in r or "profit" in r:
        badges.append('<span class="badge badge-target me-1">TARGET</span>')
    if "200%" in r or "stop" in r:
        badges.append('<span class="badge badge-stop me-1">STOP</span>')
    if "21 dte" in r or "dte" in r:
        badges.append('<span class="badge badge-dte me-1">21 DTE</span>')
    if "delta" in r:
        badges.append('<span class="badge badge-delta me-1">DELTA</span>')
    return "".join(badges) or reason


def _reprice_table(results: list[dict], pos_map: dict) -> str:
    if not results:
        return '<div class="alert alert-info">No positions to reprice.</div>'

    rows = ""
    for r in results:
        pos = pos_map.get(r.get("position_id", -1), {})
        ticker = r.get("ticker", pos.get("ticker", "—"))
        spot = r.get("spot")
        cost = r.get("cost_to_close")
        unrl = r.get("unrealized_pnl")
        entry_credit = pos.get("entry_credit", 0)
        signal = "✓" if r.get("exit_signal_triggered") else ""
        signal_reason = r.get("exit_signal_reason", "")
        error = r.get("error", "")

        rows += f"""<tr>
            <td>{pos.get('id', '—')}</td>
            <td>{ticker}</td>
            <td>{pos.get('strategy', '—')}</td>
            <td>{pos.get('entry_date', '—')}</td>
            <td>${entry_credit:.2f}</td>
            <td>{"$" + f"{spot:.2f}" if spot is not None else "—"}</td>
            <td>{"$" + f"{cost:.2f}" if cost is not None else "—"}</td>
            <td class="{_sign_class(unrl or 0)}">{"$" + f"{(unrl or 0) * 100:+.2f}" if unrl is not None else "—"}</td>
            <td>{"⚠ " + signal_reason if signal_reason else ("✓" if signal == "✓" else "")}</td>
            <td class="text-danger">{error}</td>
        </tr>"""

    return f"""<div class="table-responsive">
    <table class="table table-sm table-striped">
        <thead><tr>
            <th>ID</th><th>Ticker</th><th>Strategy</th><th>Entry Date</th>
            <th>Entry Credit</th><th>Spot</th><th>Cost to Close</th>
            <th>Unrl P&L (1 lot)</th><th>Signal</th><th>Error</th>
        </tr></thead>
        <tbody>{rows}</tbody>
    </table></div>"""


def _candidates_table(candidates: list[dict]) -> str:
    import json as _json
    if not candidates:
        return '<div class="alert alert-warning">No pending candidates. Run the scheduler to screen.</div>'

    rows = ""
    for c in candidates:
        try:
            legs = _json.loads(c.get("legs_json") or "{}")
        except Exception:
            legs = {}
        try:
            greeks = _json.loads(c.get("greeks_json") or "{}")
        except Exception:
            greeks = {}

        exp = legs.get("expiration", "—")
        short_k = legs.get("short_strike", "—")
        long_k = legs.get("long_strike", "—")
        width = legs.get("width", "—")
        credit = c.get("credit", 0)
        max_risk = c.get("max_risk", 0)
        cr_width = credit / width if width and width != "—" and width > 0 else 0
        pop = c.get("pop", 0)
        short_d = greeks.get("short_delta", "—")
        net_d = greeks.get("net_delta", "—")
        net_t = greeks.get("net_theta", "—")
        net_v = greeks.get("net_vega", "—")
        iv = c.get("iv") or 0

        rows += f"""<tr>
            <td>{c['ticker']}</td>
            <td>{c['strategy']}</td>
            <td>{exp}</td>
            <td>{short_k}</td><td>{long_k}</td>
            <td>{width}</td>
            <td>${credit:.2f}</td>
            <td>{cr_width:.1%}</td>
            <td>{pop:.1%}</td>
            <td>{short_d if isinstance(short_d, str) else f"{short_d:.3f}"}</td>
            <td>{net_d if isinstance(net_d, str) else f"{net_d:+.3f}"}</td>
            <td>{net_t if isinstance(net_t, str) else f"{net_t:+.3f}"}</td>
            <td>{net_v if isinstance(net_v, str) else f"{net_v:+.3f}"}</td>
            <td>{iv:.1%}</td>
            <td><small class="text-muted">{c.get('generated_at', '')[:16]}</small></td>
        </tr>"""

    return f"""<div class="table-responsive">
    <table class="table table-sm table-striped table-hover">
        <thead><tr>
            <th>Ticker</th><th>Strategy</th><th>Exp</th><th>Short K</th><th>Long K</th>
            <th>Width</th><th>Credit</th><th>Cr/W</th><th>PoP</th>
            <th>Short Δ</th><th>Net Δ</th><th>Net Θ</th><th>Net V</th><th>IV</th><th>Generated</th>
        </tr></thead>
        <tbody>{rows}</tbody>
    </table></div>"""


def _strategy_breakdown_table(breakdown: list[dict]) -> str:
    if not breakdown:
        return '<div class="alert alert-info">No strategy data.</div>'

    rows = ""
    for sb in breakdown:
        pnl = sb.get("total_pnl", 0)
        rows += f"""<tr>
            <td>{sb['strategy']}</td>
            <td>{sb.get('open', 0)}</td>
            <td>{sb.get('pending', 0)}</td>
            <td>{sb.get('closed', 0)}</td>
            <td class="{_sign_class(pnl)}">${pnl * 100:+.2f}</td>
            <td>{sb.get('net_delta_open', 0):+.3f}</td>
        </tr>"""

    return f"""<div class="table-responsive">
    <table class="table table-sm table-striped">
        <thead><tr>
            <th>Strategy</th><th>Open</th><th>Pending</th><th>Closed</th>
            <th>Total P&L (1 lot)</th><th>Net Delta (Open)</th>
        </tr></thead>
        <tbody>{rows}</tbody>
    </table></div>"""


def _pnl_by_underlying_table(data: list[dict]) -> str:
    if not data:
        return '<div class="alert alert-info">No trade history yet.</div>'

    rows = ""
    for d in data:
        pnl = d.get("total_pnl", 0)
        rows += f"""<tr>
            <td>{d['ticker']}</td>
            <td>{d.get('trade_count', 0)}</td>
            <td>{d.get('win_rate', 0):.1%}</td>
            <td class="{_sign_class(pnl)}">${pnl * 100:+.2f}</td>
        </tr>"""

    return f"""<div class="table-responsive">
    <table class="table table-sm table-striped">
        <thead><tr>
            <th>Ticker</th><th>Trades</th><th>Win Rate</th><th>Total P&L (1 lot)</th>
        </tr></thead>
        <tbody>{rows}</tbody>
    </table></div>"""
