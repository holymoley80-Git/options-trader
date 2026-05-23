"""Yahoo Finance helpers for options market data (15-min delayed, unofficial)."""

import yfinance as yf


def get_expirations(underlying: str, skip_today: bool = True) -> list[str]:
    """Return available expiration dates (YYYY-MM-DD) from Yahoo Finance."""
    from datetime import date
    ticker = yf.Ticker(underlying)
    exps = list(ticker.options)
    if skip_today:
        today = date.today().isoformat()
        exps = [e for e in exps if e > today]
    return exps


def get_nearest_expiration(underlying: str, target_dte: int) -> str | None:
    """Return the expiration date whose DTE is closest to target_dte."""
    from datetime import date
    today = date.today()
    exps = get_expirations(underlying)
    if not exps:
        return None
    return min(exps, key=lambda e: abs((date.fromisoformat(e) - today).days - target_dte))


def build_chain_from_yahoo(
    underlying: str,
    expiration: str,
    spot: float,
    r: float,
    contract_type: str | None = None,
    strike_price_gte: float | None = None,
    strike_price_lte: float | None = None,
) -> list[dict]:
    """
    Build a full options chain dict list directly from Yahoo Finance.
    Greeks are computed locally via Black-Scholes using Yahoo's IV.
    """
    from datetime import date
    from options_trader.greeks import calculate_greeks

    mkt_data = get_yf_chain(underlying, expiration)
    today = date.today()
    exp_date = date.fromisoformat(expiration)
    tte_days = (exp_date - today).days
    if tte_days <= 0:
        return []
    T = tte_days / 365

    results = []
    for (strike, ctype), mkt in sorted(mkt_data.items()):
        if contract_type and ctype != contract_type:
            continue
        if strike_price_gte is not None and strike < strike_price_gte:
            continue
        if strike_price_lte is not None and strike > strike_price_lte:
            continue

        iv = mkt.get("iv") or 0.30
        try:
            g = calculate_greeks(spot, strike, T, r, iv, option_type=ctype)
            greeks_dict = {"delta": g.delta, "gamma": g.gamma, "theta": g.theta,
                           "vega": g.vega, "iv": iv}
        except Exception:
            greeks_dict = {"delta": None, "gamma": None, "theta": None, "vega": None, "iv": iv}

        results.append({
            "ticker": None,
            "underlying": underlying,
            "strike": strike,
            "expiration": expiration,
            "contract_type": ctype,
            "bid": mkt.get("bid"),
            "ask": mkt.get("ask"),
            "last_price": mkt.get("last_price"),
            "volume": mkt.get("volume"),
            "open_interest": mkt.get("open_interest"),
            **greeks_dict,
        })
    return results


def get_yf_chain(underlying: str, expiration: str) -> dict[tuple[float, str], dict]:
    """
    Fetch options chain for one expiration from Yahoo Finance.
    Returns a dict keyed by (strike, contract_type) -> market data dict.
    """
    ticker = yf.Ticker(underlying)
    try:
        chain = ticker.option_chain(expiration)
    except Exception as e:
        raise RuntimeError(f"Yahoo Finance options fetch failed for {underlying} {expiration}: {e}")

    result: dict[tuple[float, str], dict] = {}
    for row in chain.calls.itertuples(index=False):
        result[(row.strike, "call")] = {
            "iv": row.impliedVolatility if row.impliedVolatility and row.impliedVolatility > 0 else None,
            "bid": row.bid if row.bid and row.bid > 0 else None,
            "ask": row.ask if row.ask and row.ask > 0 else None,
            "volume": int(row.volume) if row.volume and not _isnan(row.volume) else None,
            "open_interest": int(row.openInterest) if row.openInterest and not _isnan(row.openInterest) else None,
            "last_price": row.lastPrice if row.lastPrice and row.lastPrice > 0 else None,
        }
    for row in chain.puts.itertuples(index=False):
        result[(row.strike, "put")] = {
            "iv": row.impliedVolatility if row.impliedVolatility and row.impliedVolatility > 0 else None,
            "bid": row.bid if row.bid and row.bid > 0 else None,
            "ask": row.ask if row.ask and row.ask > 0 else None,
            "volume": int(row.volume) if row.volume and not _isnan(row.volume) else None,
            "open_interest": int(row.openInterest) if row.openInterest and not _isnan(row.openInterest) else None,
            "last_price": row.lastPrice if row.lastPrice and row.lastPrice > 0 else None,
        }
    return result


def get_spot_price(underlying: str) -> float:
    """Return the most recent price from Yahoo Finance (intraday, ~15 min delay)."""
    ticker = yf.Ticker(underlying)
    info = ticker.fast_info
    price = getattr(info, "last_price", None) or getattr(info, "regular_market_price", None)
    if not price:
        raise ValueError(f"Could not retrieve spot price for {underlying} from Yahoo Finance")
    return float(price)


def _isnan(val) -> bool:
    try:
        import math
        return math.isnan(val)
    except (TypeError, ValueError):
        return False
