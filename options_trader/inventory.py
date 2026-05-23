"""Candidate slot management and position repricing."""

import json
import os
from datetime import date, datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Config from .env
# ---------------------------------------------------------------------------

TARGET_CANDIDATES = int(os.getenv("TARGET_CANDIDATES", "7"))
TARGET_POSITIONS = int(os.getenv("TARGET_POSITIONS", "10"))
SCREEN_DTE = int(os.getenv("SCREEN_DTE", "30"))
SCREEN_SPREAD_TYPE = os.getenv("SCREEN_SPREAD_TYPE", "short_put_vertical")
SCREEN_WIDTHS = [float(w) for w in os.getenv("SCREEN_WIDTHS", "5").split(",")]
SCREEN_MIN_POP = float(os.getenv("SCREEN_MIN_POP", "0.65"))


# ---------------------------------------------------------------------------
# Candidate slot filling
# ---------------------------------------------------------------------------

def fill_candidate_slots() -> int:
    """Screen watchlist and insert candidates until TARGET_CANDIDATES slots are full.

    Strategy per ticker is chosen by market context (IV level, price vs SMA20/52w range).
    Only one candidate per ticker. Skips tickers already pending.
    Returns number of candidates added.
    """
    from options_trader.db import get_candidates, insert_candidate, insert_position
    from options_trader.watchlist import WATCHLIST
    import logging
    log = logging.getLogger(__name__)

    pending = get_candidates(status="pending")
    slots_needed = TARGET_CANDIDATES - len(pending)
    if slots_needed <= 0:
        return 0

    pending_tickers = {c["ticker"] for c in pending}
    added = 0

    for ticker in WATCHLIST:
        if added >= slots_needed:
            break
        if ticker in pending_tickers:
            continue

        try:
            result = _best_candidate_for_ticker(ticker)
            if result is None:
                continue

            strategy, spread, spot = result
            legs, greeks, credit, max_risk, pop, iv = _extract_candidate_fields(strategy, spread)

            cid = insert_candidate(
                ticker=ticker,
                strategy=strategy,
                legs_json_str=json.dumps(legs),
                credit=credit,
                max_risk=max_risk,
                pop=pop,
                greeks_json_str=json.dumps(greeks),
                iv=iv,
            )
            # Auto-create a paper position to track this proposal's outcome
            insert_position(
                candidate_id=cid,
                ticker=ticker,
                strategy=strategy,
                legs_json_str=json.dumps(legs),
                entry_credit=credit,
                entry_greeks_json_str=json.dumps(greeks),
                entry_price_underlying=spot,
                paper=1,
                entry_iv=iv,
            )
            pending_tickers.add(ticker)
            added += 1
            log.debug("fill_candidate_slots: added %s as %s", ticker, strategy)

        except Exception as exc:
            log.warning("fill_candidate_slots: skipping %s — %s", ticker, exc)
            continue

    return added


def _best_candidate_for_ticker(ticker: str) -> tuple[str, object, float] | None:
    """
    Assess market context for ticker and return (strategy, best_spread_or_condor, spot).
    Falls back to short_put_vertical if the context-suggested strategy yields nothing.
    Returns None if no spread passes the screen.
    """
    from options_trader.polygon_client import get_options_chain, get_underlying_price
    from options_trader.yahoo_client import get_nearest_expiration
    from options_trader.market_context import get_market_context
    from options_trader.spread_screener import (
        build_spread_pairs, screen_spreads,
        build_iron_condors, screen_iron_condors,
    )

    spot = get_underlying_price(ticker)
    target_exp = get_nearest_expiration(ticker, SCREEN_DTE)
    if not target_exp:
        return None

    contracts = get_options_chain(
        underlying=ticker,
        expiration_date=target_exp,
        strike_price_gte=spot * 0.80,
        strike_price_lte=spot * 1.20,
    )
    if not contracts:
        return None

    ctx = get_market_context(ticker, spot, contracts)
    strategy = ctx.suggested_strategy

    # Iron condor path
    if strategy == "iron_condor":
        condors = build_iron_condors(contracts, SCREEN_WIDTHS)
        screened = screen_iron_condors(condors, min_pop=0.50)
        if screened:
            return strategy, screened[0], spot
        # Fall through to default credit spread
        strategy = "short_put_vertical"

    # Vertical spread path (credit or debit)
    min_pop = SCREEN_MIN_POP if "short_" in strategy else 0.0
    pairs = build_spread_pairs(contracts, strategy, SCREEN_WIDTHS)
    screened = screen_spreads(pairs, strategy, min_pop=min_pop)
    if screened:
        return strategy, screened[0], spot

    # Last resort: if a directional or bearish strategy came back empty, try short_put_vertical
    if strategy != "short_put_vertical":
        pairs = build_spread_pairs(contracts, "short_put_vertical", SCREEN_WIDTHS)
        screened = screen_spreads(pairs, "short_put_vertical", min_pop=SCREEN_MIN_POP)
        if screened:
            return "short_put_vertical", screened[0], spot

    return None


def _extract_candidate_fields(
    strategy: str, spread: object
) -> tuple[dict, dict, float, float, float, float]:
    """Return (legs, greeks, credit, max_risk, pop, iv) for any spread type."""
    from options_trader.spread_screener import IronCondor

    if isinstance(spread, IronCondor):
        legs = {
            "type": "iron_condor",
            "short_put_strike": spread.short_put,
            "long_put_strike": spread.long_put,
            "short_call_strike": spread.short_call,
            "long_call_strike": spread.long_call,
            "expiration": spread.expiration,
            "width": spread.width,
        }
        greeks = {
            "net_delta": spread.net_delta,
            "net_gamma": spread.net_gamma,
            "net_theta": spread.net_theta,
            "net_vega": spread.net_vega,
            "short_put_delta": spread.short_put_delta,
            "short_call_delta": spread.short_call_delta,
            "short_iv": spread.short_iv,
        }
        return legs, greeks, spread.net_credit, spread.max_risk, spread.pop_approx, spread.short_iv

    # Spread (vertical)
    legs = {
        "type": strategy,
        "short_strike": spread.short_strike,
        "long_strike": spread.long_strike,
        "expiration": spread.expiration,
        "width": spread.width,
    }
    greeks = {
        "net_delta": spread.net_delta,
        "net_gamma": spread.net_gamma,
        "net_theta": spread.net_theta,
        "net_vega": spread.net_vega,
        "short_delta": spread.short_delta_abs,
        "short_iv": spread.short_iv,
        "long_iv": spread.long_iv,
    }
    return legs, greeks, spread.net_credit, spread.max_risk, spread.pop_approx, spread.short_iv


# ---------------------------------------------------------------------------
# Accept / Reject
# ---------------------------------------------------------------------------

def accept_candidate(candidate_id: int) -> int:
    """Convert a pending candidate to an open position.

    Updates candidate status to 'accepted', inserts a position row, refills
    candidate slots, and returns the new position_id.
    """
    from options_trader.db import (
        get_candidates, insert_position, update_candidate_status, get_conn
    )

    # Fetch candidate
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM candidates WHERE id=?", (candidate_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"Candidate {candidate_id} not found")
        cand = dict(row)

    # Get current spot price for entry
    entry_spot = None
    try:
        from options_trader.polygon_client import get_underlying_price
        entry_spot = get_underlying_price(cand["ticker"])
    except Exception:
        pass

    entry_greeks = json.loads(cand.get("greeks_json") or "{}")
    pos_id = insert_position(
        candidate_id=candidate_id,
        ticker=cand["ticker"],
        strategy=cand["strategy"],
        legs_json_str=cand["legs_json"],
        entry_credit=cand["credit"],
        entry_greeks_json_str=cand["greeks_json"],
        entry_price_underlying=entry_spot,
        grade=cand.get("grade"),
        entry_iv=cand.get("iv") or entry_greeks.get("short_iv"),
    )

    update_candidate_status(candidate_id, "accepted")
    fill_candidate_slots()
    return pos_id


def reject_candidate(candidate_id: int, reason: str) -> None:
    """Mark a candidate rejected and refill the slot."""
    from options_trader.db import update_candidate_status

    update_candidate_status(candidate_id, "rejected", reject_reason=reason)
    fill_candidate_slots()


# ---------------------------------------------------------------------------
# Position repricing
# ---------------------------------------------------------------------------

def reprice_position(pos: dict) -> dict:
    """Fetch current market data for an open position and evaluate exit signals.

    Returns a dict with:
        spot, cost_to_close, unrealized_pnl, current_greeks,
        exit_signal_triggered (bool), exit_signal_reason (str|None)
    """
    legs = json.loads(pos["legs_json"])
    spread_type = legs.get("type", pos["strategy"])

    if spread_type == "iron_condor":
        return _reprice_iron_condor(pos, legs)

    entry_credit = pos["entry_credit"]

    short_strike = legs["short_strike"]
    long_strike = legs["long_strike"]
    expiration = legs["expiration"]

    result = {
        "position_id": pos["id"],
        "ticker": pos["ticker"],
        "spot": None,
        "cost_to_close": None,
        "unrealized_pnl": None,
        "current_greeks": None,
        "exit_signal_triggered": False,
        "exit_signal_reason": None,
    }

    # Fetch current spot
    try:
        from options_trader.polygon_client import get_underlying_price
        spot = get_underlying_price(pos["ticker"])
        result["spot"] = spot
    except Exception:
        return result

    # Fetch current chain for this expiration
    try:
        from options_trader.polygon_client import get_options_chain
        chain = get_options_chain(
            underlying=pos["ticker"],
            expiration_date=expiration,
            strike_price_gte=min(short_strike, long_strike) * 0.99,
            strike_price_lte=max(short_strike, long_strike) * 1.01,
        )
    except Exception:
        return result

    # Index by strike
    chain_by_strike: dict[float, dict] = {}
    for c in chain:
        if c.get("contract_type") == "put" or "put" in spread_type:
            if c.get("contract_type") == "put":
                chain_by_strike[c["strike"]] = c
        if c.get("contract_type") == "call" or "call" in spread_type:
            if c.get("contract_type") == "call":
                chain_by_strike[c["strike"]] = c

    # Re-fetch with correct contract type filter
    ctype = "put" if "put" in spread_type else "call"
    try:
        chain2 = get_options_chain(
            underlying=pos["ticker"],
            expiration_date=expiration,
            strike_price_gte=min(short_strike, long_strike) * 0.99,
            strike_price_lte=max(short_strike, long_strike) * 1.01,
        )
        chain_by_strike = {
            c["strike"]: c
            for c in chain2
            if c.get("contract_type") == ctype
        }
    except Exception:
        return result

    short_contract = chain_by_strike.get(short_strike)
    long_contract = chain_by_strike.get(long_strike)

    if short_contract is None or long_contract is None:
        # Strike not found in chain — skip exit signal checks per spec
        return result

    # Cost to close: buy back short at ask, sell long at bid
    short_ask = short_contract.get("ask")
    long_bid = long_contract.get("bid")

    if short_ask is None or long_bid is None:
        return result

    cost_to_close = short_ask - long_bid  # net debit to close
    unrealized_pnl = entry_credit - cost_to_close

    result["cost_to_close"] = round(cost_to_close, 4)
    result["unrealized_pnl"] = round(unrealized_pnl, 4)

    # Current Greeks
    current_greeks = {
        "net_delta": round(
            -(short_contract.get("delta") or 0) + (long_contract.get("delta") or 0), 4
        ),
        "net_gamma": round(
            -(short_contract.get("gamma") or 0) + (long_contract.get("gamma") or 0), 4
        ),
        "net_theta": round(
            -(short_contract.get("theta") or 0) + (long_contract.get("theta") or 0), 4
        ),
        "net_vega": round(
            -(short_contract.get("vega") or 0) + (long_contract.get("vega") or 0), 4
        ),
        "short_delta": round(abs(short_contract.get("delta") or 0), 4),
        "short_iv": short_contract.get("iv") or 0,
        "long_iv": long_contract.get("iv") or 0,
    }
    result["current_greeks"] = current_greeks

    # --- Exit signal evaluation ---
    today = date.today()
    exp_date = date.fromisoformat(expiration)
    tte_days = (exp_date - today).days

    signals = []

    # 50% profit target
    if unrealized_pnl >= entry_credit * 0.50:
        signals.append("50% profit target")

    # 200% stop loss
    if cost_to_close >= entry_credit * 2.0:
        signals.append("200% stop loss")

    # 21 DTE
    if tte_days <= 21:
        signals.append("21 DTE")

    # Delta breach (short leg delta > 0.45)
    short_delta_current = abs(short_contract.get("delta") or 0)
    if short_delta_current > 0.45:
        signals.append(f"delta breach ({short_delta_current:.2f})")

    if signals:
        result["exit_signal_triggered"] = True
        result["exit_signal_reason"] = "; ".join(signals)

    return result


# ---------------------------------------------------------------------------
# Iron condor repricing
# ---------------------------------------------------------------------------

def _reprice_iron_condor(pos: dict, legs: dict) -> dict:
    """Reprice a 4-leg iron condor position."""
    from options_trader.polygon_client import get_options_chain, get_underlying_price

    entry_credit = pos["entry_credit"]
    expiration = legs["expiration"]
    short_put = legs["short_put_strike"]
    long_put = legs["long_put_strike"]
    short_call = legs["short_call_strike"]
    long_call = legs["long_call_strike"]

    result = {
        "position_id": pos["id"],
        "ticker": pos["ticker"],
        "spot": None,
        "cost_to_close": None,
        "unrealized_pnl": None,
        "current_greeks": None,
        "exit_signal_triggered": False,
        "exit_signal_reason": None,
    }

    try:
        spot = get_underlying_price(pos["ticker"])
        result["spot"] = spot
    except Exception:
        return result

    def _fetch_leg(strike, ctype):
        try:
            chain = get_options_chain(
                underlying=pos["ticker"],
                expiration_date=expiration,
                strike_price_gte=strike * 0.99,
                strike_price_lte=strike * 1.01,
            )
            for c in chain:
                if c.get("contract_type") == ctype and c.get("strike") == strike:
                    return c
        except Exception:
            pass
        return None

    sp = _fetch_leg(short_put, "put")
    lp = _fetch_leg(long_put, "put")
    sc = _fetch_leg(short_call, "call")
    lc = _fetch_leg(long_call, "call")

    if not all([sp, lp, sc, lc]):
        return result

    sp_ask = sp.get("ask")
    lp_bid = lp.get("bid")
    sc_ask = sc.get("ask")
    lc_bid = lc.get("bid")

    if any(v is None for v in [sp_ask, lp_bid, sc_ask, lc_bid]):
        return result

    cost_to_close = round((sp_ask - lp_bid) + (sc_ask - lc_bid), 4)
    unrealized_pnl = round(entry_credit - cost_to_close, 4)

    result["cost_to_close"] = cost_to_close
    result["unrealized_pnl"] = unrealized_pnl
    result["current_greeks"] = {
        "net_delta": round(
            -(sp.get("delta") or 0) + (lp.get("delta") or 0)
            -(sc.get("delta") or 0) + (lc.get("delta") or 0), 4
        ),
        "net_theta": round(
            -(sp.get("theta") or 0) + (lp.get("theta") or 0)
            -(sc.get("theta") or 0) + (lc.get("theta") or 0), 4
        ),
        "net_vega": round(
            -(sp.get("vega") or 0) + (lp.get("vega") or 0)
            -(sc.get("vega") or 0) + (lc.get("vega") or 0), 4
        ),
        "short_put_delta": round(abs(sp.get("delta") or 0), 4),
        "short_call_delta": round(abs(sc.get("delta") or 0), 4),
    }

    today = date.today()
    exp_date = date.fromisoformat(expiration)
    tte_days = (exp_date - today).days
    signals = []

    if unrealized_pnl >= entry_credit * 0.50:
        signals.append("50% profit target")
    if cost_to_close >= entry_credit * 2.0:
        signals.append("200% stop loss")
    if tte_days <= 21:
        signals.append("21 DTE")
    for leg_label, leg in [("short put", sp), ("short call", sc)]:
        d = abs(leg.get("delta") or 0)
        if d > 0.45:
            signals.append(f"delta breach {leg_label} ({d:.2f})")

    if signals:
        result["exit_signal_triggered"] = True
        result["exit_signal_reason"] = "; ".join(signals)

    return result


# ---------------------------------------------------------------------------
# Daily reprice run
# ---------------------------------------------------------------------------

def run_daily_reprice() -> list[dict]:
    """Reprice all open positions, insert daily_snapshots. Returns list of reprice dicts."""
    from options_trader.db import get_positions, insert_snapshot

    today_str = date.today().isoformat()
    open_positions = get_positions(status="open")

    results = []
    for pos in open_positions:
        try:
            r = reprice_position(pos)
            insert_snapshot(
                position_id=pos["id"],
                date_str=today_str,
                spot=r.get("spot"),
                greeks_json_str=json.dumps(r["current_greeks"]) if r.get("current_greeks") else None,
                unrealized_pnl=r.get("unrealized_pnl"),
                signal_triggered=r.get("exit_signal_triggered", False),
                signal_reason=r.get("exit_signal_reason"),
            )
            results.append(r)
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning(
                "run_daily_reprice: error repricing position %s (%s) — %s",
                pos["id"], pos["ticker"], exc,
            )
            results.append({
                "position_id": pos["id"],
                "ticker": pos["ticker"],
                "error": str(exc),
            })

    return results


# ---------------------------------------------------------------------------
# Close position
# ---------------------------------------------------------------------------

def close_position(position_id: int, exit_debit: float, reason: str) -> None:
    """Close a position: compute pnl = entry_credit - exit_debit, update status."""
    from options_trader.db import get_positions, update_position_close

    positions = get_positions()
    pos = next((p for p in positions if p["id"] == position_id), None)
    if pos is None:
        raise ValueError(f"Position {position_id} not found")

    pnl = pos["entry_credit"] - exit_debit
    update_position_close(
        id=position_id,
        exit_debit=exit_debit,
        exit_greeks_json_str="{}",
        pnl=round(pnl, 4),
        exit_reason=reason,
    )
