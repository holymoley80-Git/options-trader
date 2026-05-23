"""Analytics and reporting queries against the SQLite database."""

import json
from datetime import date, datetime, timedelta
from typing import Optional

from options_trader.db import get_conn


def summary() -> dict:
    """Dashboard summary statistics."""
    today_str = date.today().isoformat()

    with get_conn() as conn:
        # Open positions count
        open_positions = conn.execute(
            "SELECT COUNT(*) FROM positions WHERE status='open'"
        ).fetchone()[0]

        # Pending candidates count
        pending_candidates = conn.execute(
            "SELECT COUNT(*) FROM candidates WHERE status='pending'"
        ).fetchone()[0]

        # Total closed trades
        total_closed = conn.execute(
            "SELECT COUNT(*) FROM positions WHERE status='closed'"
        ).fetchone()[0]

        # Win rate (all time)
        closed_rows = conn.execute(
            "SELECT pnl FROM positions WHERE status='closed' AND pnl IS NOT NULL"
        ).fetchall()
        win_rate_all = _calc_win_rate(closed_rows)

        # Win rate 90d
        cutoff_90 = (date.today() - timedelta(days=90)).isoformat()
        closed_90 = conn.execute(
            "SELECT pnl FROM positions WHERE status='closed' AND pnl IS NOT NULL "
            "AND exit_date >= ?",
            (cutoff_90,),
        ).fetchall()
        win_rate_90d = _calc_win_rate(closed_90)

        # P&L windows
        pnl_30d = _pnl_window(conn, 30)
        pnl_60d = _pnl_window(conn, 60)
        pnl_90d = _pnl_window(conn, 90)

        # Average P&L per trade
        avg_pnl = 0.0
        if closed_rows:
            total_pnl = sum(r[0] for r in closed_rows if r[0] is not None)
            avg_pnl = total_pnl / len(closed_rows) if closed_rows else 0.0

        # Portfolio delta: sum net_delta from latest snapshots for open positions,
        # or from entry_greeks if no snapshot
        open_pos = conn.execute(
            "SELECT id, entry_greeks_json FROM positions WHERE status='open'"
        ).fetchall()

        portfolio_delta = 0.0
        for pos_row in open_pos:
            pos_id = pos_row[0]
            entry_greeks_json = pos_row[1]

            snap = conn.execute(
                "SELECT current_greeks_json FROM daily_snapshots "
                "WHERE position_id=? ORDER BY date DESC, id DESC LIMIT 1",
                (pos_id,),
            ).fetchone()

            if snap and snap[0]:
                try:
                    g = json.loads(snap[0])
                    portfolio_delta += g.get("net_delta", 0.0)
                    continue
                except (json.JSONDecodeError, TypeError):
                    pass

            # Fall back to entry greeks
            if entry_greeks_json:
                try:
                    g = json.loads(entry_greeks_json)
                    portfolio_delta += g.get("net_delta", 0.0)
                except (json.JSONDecodeError, TypeError):
                    pass

        # Exit signals active today
        exit_signals_active = conn.execute(
            """SELECT COUNT(*) FROM daily_snapshots ds
               JOIN positions p ON p.id = ds.position_id
               WHERE p.status='open' AND ds.date=? AND ds.exit_signal_triggered=1""",
            (today_str,),
        ).fetchone()[0]

    return {
        "open_positions": open_positions,
        "pending_candidates": pending_candidates,
        "total_closed_trades": total_closed,
        "win_rate_all": round(win_rate_all, 4),
        "win_rate_90d": round(win_rate_90d, 4),
        "pnl_30d": round(pnl_30d, 4),
        "pnl_60d": round(pnl_60d, 4),
        "pnl_90d": round(pnl_90d, 4),
        "avg_pnl_per_trade": round(avg_pnl, 4),
        "portfolio_delta": round(portfolio_delta, 4),
        "exit_signals_active": exit_signals_active,
    }


def win_rate(strategy: str = None, days: int = None) -> float:
    """Return fraction of closed positions that are profitable."""
    with get_conn() as conn:
        sql = "SELECT pnl FROM positions WHERE status='closed' AND pnl IS NOT NULL"
        params = []
        if strategy:
            sql += " AND strategy=?"
            params.append(strategy)
        if days:
            cutoff = (date.today() - timedelta(days=days)).isoformat()
            sql += " AND exit_date >= ?"
            params.append(cutoff)
        rows = conn.execute(sql, params).fetchall()
    return _calc_win_rate(rows)


def pnl_by_underlying() -> list[dict]:
    """P&L broken down by underlying ticker."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT ticker,
                      SUM(CASE WHEN status='closed' THEN pnl ELSE 0 END) as total_pnl,
                      SUM(CASE WHEN status='closed' THEN 1 ELSE 0 END) as trade_count,
                      SUM(CASE WHEN status='closed' AND pnl > 0 THEN 1 ELSE 0 END) as wins
               FROM positions
               GROUP BY ticker
               ORDER BY total_pnl DESC""",
        ).fetchall()
    result = []
    for r in rows:
        tc = r[2] or 0
        wins = r[3] or 0
        result.append({
            "ticker": r[0],
            "total_pnl": round(r[1] or 0, 4),
            "trade_count": tc,
            "win_rate": round(wins / tc, 4) if tc > 0 else 0.0,
        })
    return result


def pnl_by_strategy() -> list[dict]:
    """P&L broken down by strategy."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT strategy,
                      SUM(CASE WHEN status='closed' THEN pnl ELSE 0 END) as total_pnl,
                      SUM(CASE WHEN status='closed' THEN 1 ELSE 0 END) as trade_count,
                      SUM(CASE WHEN status='closed' AND pnl > 0 THEN 1 ELSE 0 END) as wins,
                      AVG(CASE WHEN status='closed' THEN entry_credit ELSE NULL END) as avg_credit,
                      AVG(CASE WHEN status='closed' THEN (
                              CAST(json_extract(legs_json, '$.width') AS REAL) - entry_credit
                          ) ELSE NULL END) as avg_max_risk
               FROM positions
               GROUP BY strategy
               ORDER BY total_pnl DESC""",
        ).fetchall()
    result = []
    for r in rows:
        tc = r[2] or 0
        wins = r[3] or 0
        result.append({
            "strategy": r[0],
            "total_pnl": round(r[1] or 0, 4),
            "trade_count": tc,
            "win_rate": round(wins / tc, 4) if tc > 0 else 0.0,
            "avg_credit": round(r[4] or 0, 4),
            "avg_max_risk": round(r[5] or 0, 4),
        })
    return result


def rolling_pnl(days: int = 30) -> float:
    """Sum of pnl for positions closed in last N days."""
    with get_conn() as conn:
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        row = conn.execute(
            "SELECT SUM(pnl) FROM positions WHERE status='closed' AND exit_date >= ?",
            (cutoff,),
        ).fetchone()
    return round(row[0] or 0.0, 4)


def avg_dte_at_entry() -> float | None:
    """Average days between entry_date and expiration (from legs_json)."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT entry_date, legs_json FROM positions WHERE status='closed'"
        ).fetchall()

    if not rows:
        return None

    deltas = []
    for r in rows:
        try:
            legs = json.loads(r[1])
            exp = legs.get("expiration")
            if exp:
                d = (date.fromisoformat(exp) - date.fromisoformat(r[0])).days
                deltas.append(d)
        except Exception:
            pass

    return round(sum(deltas) / len(deltas), 1) if deltas else None


def avg_dte_at_exit() -> float | None:
    """Average days between exit_date and expiration (from legs_json)."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT exit_date, legs_json FROM positions WHERE status='closed' AND exit_date IS NOT NULL"
        ).fetchall()

    if not rows:
        return None

    deltas = []
    for r in rows:
        try:
            legs = json.loads(r[1])
            exp = legs.get("expiration")
            if exp and r[0]:
                d = (date.fromisoformat(exp) - date.fromisoformat(r[0])).days
                deltas.append(d)
        except Exception:
            pass

    return round(sum(deltas) / len(deltas), 1) if deltas else None


def iv_entry_vs_outcome() -> list[dict]:
    """Bucket closed trades by entry IV. Returns [{iv_bucket, trade_count, win_rate, avg_pnl}]."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT greeks_json, pnl FROM candidates c "
            "JOIN positions p ON p.candidate_id = c.id "
            "WHERE p.status='closed' AND p.pnl IS NOT NULL"
        ).fetchall()

    buckets: dict[str, list[float]] = {
        "low (<0.20)": [],
        "medium (0.20-0.35)": [],
        "high (>0.35)": [],
    }

    for r in rows:
        try:
            greeks = json.loads(r[0])
            iv = greeks.get("short_iv", 0.0) or 0.0
            pnl = r[1]
            if iv < 0.20:
                buckets["low (<0.20)"].append(pnl)
            elif iv <= 0.35:
                buckets["medium (0.20-0.35)"].append(pnl)
            else:
                buckets["high (>0.35)"].append(pnl)
        except Exception:
            pass

    result = []
    for bucket, pnls in buckets.items():
        if not pnls:
            continue
        wins = sum(1 for p in pnls if p > 0)
        result.append({
            "iv_bucket": bucket,
            "trade_count": len(pnls),
            "win_rate": round(wins / len(pnls), 4),
            "avg_pnl": round(sum(pnls) / len(pnls), 4),
        })
    return result


def strategy_breakdown() -> list[dict]:
    """Count of open/pending/closed per strategy, with sum net_delta for open."""
    with get_conn() as conn:
        # Positions counts by strategy and status
        pos_rows = conn.execute(
            """SELECT strategy,
                      SUM(CASE WHEN status='open' THEN 1 ELSE 0 END) as open_count,
                      SUM(CASE WHEN status='closed' THEN 1 ELSE 0 END) as closed_count,
                      SUM(CASE WHEN status='closed' THEN pnl ELSE 0 END) as total_pnl
               FROM positions
               GROUP BY strategy"""
        ).fetchall()

        # Pending candidates per strategy
        cand_rows = conn.execute(
            """SELECT strategy, COUNT(*) as pending_count
               FROM candidates WHERE status='pending'
               GROUP BY strategy"""
        ).fetchall()

        # Sum net_delta for open positions (from latest snapshot or entry greeks)
        open_pos = conn.execute(
            "SELECT id, strategy, entry_greeks_json FROM positions WHERE status='open'"
        ).fetchall()

    pending_by_strategy = {r[0]: r[1] for r in cand_rows}

    # Compute per-strategy delta sums
    delta_by_strategy: dict[str, float] = {}
    for pos_row in open_pos:
        pos_id, strat, entry_greeks_json = pos_row[0], pos_row[1], pos_row[2]
        delta = 0.0
        with get_conn() as conn:
            snap = conn.execute(
                "SELECT current_greeks_json FROM daily_snapshots "
                "WHERE position_id=? ORDER BY date DESC LIMIT 1",
                (pos_id,),
            ).fetchone()
        if snap and snap[0]:
            try:
                g = json.loads(snap[0])
                delta = g.get("net_delta", 0.0)
            except Exception:
                pass
        else:
            try:
                g = json.loads(entry_greeks_json or "{}")
                delta = g.get("net_delta", 0.0)
            except Exception:
                pass
        delta_by_strategy[strat] = delta_by_strategy.get(strat, 0.0) + delta

    strategies = set()
    for r in pos_rows:
        strategies.add(r[0])
    for s in pending_by_strategy:
        strategies.add(s)

    result = []
    pos_map = {r[0]: r for r in pos_rows}
    for strat in sorted(strategies):
        pr = pos_map.get(strat)
        result.append({
            "strategy": strat,
            "open": pr[1] if pr else 0,
            "pending": pending_by_strategy.get(strat, 0),
            "closed": pr[2] if pr else 0,
            "total_pnl": round(pr[3] or 0, 4) if pr else 0.0,
            "net_delta_open": round(delta_by_strategy.get(strat, 0.0), 4),
        })
    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _calc_win_rate(rows) -> float:
    if not rows:
        return 0.0
    wins = sum(1 for r in rows if (r[0] or 0) > 0)
    return wins / len(rows)


def _pnl_window(conn, days: int) -> float:
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    row = conn.execute(
        "SELECT SUM(pnl) FROM positions WHERE status='closed' AND exit_date >= ?",
        (cutoff,),
    ).fetchone()
    return row[0] or 0.0
