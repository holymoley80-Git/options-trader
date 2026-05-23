"""Polygon.io API wrappers for options contract reference data.

Spot price and live market data (IV, bid/ask, OI, volume) are sourced from
Yahoo Finance (15-min delayed) via yahoo_client.py. Greeks are computed
locally via Black-Scholes. If a Polygon paid plan is added later, swap
get_options_chain to use list_snapshot_options_chain for real-time data.
"""

import os
from datetime import date
from typing import Optional
from dotenv import load_dotenv
from polygon import RESTClient

load_dotenv()


def get_client() -> RESTClient:
    api_key = os.getenv("POLYGON_API_KEY")
    if not api_key:
        raise EnvironmentError("POLYGON_API_KEY not set in environment or .env file")
    return RESTClient(api_key)


def get_underlying_price(ticker: str) -> float:
    """
    Fetch spot price. Tries Yahoo Finance first (intraday, ~15 min delay);
    falls back to Polygon previous close.
    """
    try:
        from options_trader.yahoo_client import get_spot_price
        return get_spot_price(ticker)
    except Exception:
        pass
    client = get_client()
    aggs = list(client.get_previous_close_agg(ticker))
    if not aggs:
        raise ValueError(f"No price data returned for {ticker}")
    return aggs[0].close


def get_options_chain(
    underlying: str,
    expiration_date: Optional[str] = None,  # "YYYY-MM-DD"
    contract_type: Optional[str] = None,     # "call" or "put"
    strike_price_gte: Optional[float] = None,
    strike_price_lte: Optional[float] = None,
    limit: int = 250,
) -> list[dict]:
    """
    Build an options chain using Yahoo Finance as the primary data source
    (IV, bid/ask, volume, OI — 15-min delayed) with Greeks computed locally
    via Black-Scholes. Polygon is used only if Yahoo fails.
    """
    from options_trader.yahoo_client import build_chain_from_yahoo, get_expirations

    spot = get_underlying_price(underlying)
    r = get_risk_free_rate()

    exp = expiration_date
    if not exp:
        expirations = get_expirations(underlying)
        if not expirations:
            raise ValueError(f"No expirations found for {underlying}")
        exp = expirations[0]

    return build_chain_from_yahoo(
        underlying=underlying,
        expiration=exp,
        spot=spot,
        r=r,
        contract_type=contract_type,
        strike_price_gte=strike_price_gte,
        strike_price_lte=strike_price_lte,
    )


def get_risk_free_rate() -> float:
    """Return a proxy risk-free rate. Falls back to 5% if unavailable."""
    try:
        import yfinance as yf
        # 13-week T-bill yield
        t = yf.Ticker("^IRX")
        rate = t.fast_info.last_price
        if rate:
            return float(rate) / 100
    except Exception:
        pass
    return 0.05
