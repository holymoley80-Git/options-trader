"""Assess market context to suggest an appropriate Passarelli strategy per ticker."""
from dataclasses import dataclass


@dataclass
class MarketContext:
    ticker: str
    spot: float
    iv_atm: float           # ATM implied volatility (decimal)
    price_52w_pct: float    # 0 = 52-week low, 1 = 52-week high
    price_vs_sma20: float   # (spot - SMA20) / SMA20
    suggested_strategy: str
    reason: str


def get_market_context(
    ticker: str,
    spot: float,
    chain: list[dict],
) -> MarketContext:
    """
    Derive market context from an already-fetched options chain plus yfinance price history.
    Passing the chain in avoids a redundant API call.
    """
    import yfinance as yf

    high_52 = low_52 = sma20 = None

    try:
        t = yf.Ticker(ticker)
        info = t.fast_info
        high_52 = getattr(info, "year_high", None)
        low_52 = getattr(info, "year_low", None)
        hist = t.history(period="30d", auto_adjust=True)
        if len(hist) >= 10:
            sma20 = float(hist["Close"].tail(20).mean())
    except Exception:
        pass

    high_52 = high_52 or spot * 1.20
    low_52 = low_52 or spot * 0.80
    sma20 = sma20 or spot

    rng = high_52 - low_52
    price_52w_pct = (spot - low_52) / rng if rng > 0 else 0.5
    price_vs_sma20 = (spot - sma20) / sma20 if sma20 else 0.0

    iv_atm = _atm_iv_from_chain(chain, spot)
    strategy, reason = _select_strategy(price_52w_pct, price_vs_sma20, iv_atm)

    return MarketContext(
        ticker=ticker,
        spot=spot,
        iv_atm=iv_atm,
        price_52w_pct=price_52w_pct,
        price_vs_sma20=price_vs_sma20,
        suggested_strategy=strategy,
        reason=reason,
    )


def _atm_iv_from_chain(chain: list[dict], spot: float) -> float:
    """Return the IV of the nearest-ATM contract in the chain."""
    if not chain:
        return 0.0
    by_distance = sorted(chain, key=lambda c: abs((c.get("strike") or 0) - spot))
    for c in by_distance[:6]:
        iv = c.get("iv") or 0.0
        if iv > 0:
            return iv
    return 0.0


def _select_strategy(
    price_52w_pct: float,
    price_vs_sma20: float,
    iv_atm: float,
) -> tuple[str, str]:
    """Return (strategy_name, human-readable reason)."""

    # Iron condor: high IV + price hovering near the mean (range-bound)
    if iv_atm >= 0.40 and abs(price_vs_sma20) <= 0.04:
        return (
            "iron_condor",
            f"IV={iv_atm:.0%} ≥ 40%, price within 4% of SMA20 — sell premium both sides",
        )

    # Short call vertical: price extended above resistance
    if price_52w_pct >= 0.85 or price_vs_sma20 >= 0.08:
        return (
            "short_call_vertical",
            f"Price at {price_52w_pct:.0%} of 52w range, {price_vs_sma20:+.1%} vs SMA20 — extended, bearish setup",
        )

    # Long call vertical: low IV + bullish momentum
    if 0 < iv_atm <= 0.20 and price_vs_sma20 >= 0.02:
        return (
            "long_call_vertical",
            f"IV={iv_atm:.0%} ≤ 20%, price {price_vs_sma20:+.1%} above SMA20 — cheap debit, directional bullish",
        )

    # Long put vertical: low IV + bearish momentum
    if 0 < iv_atm <= 0.20 and price_vs_sma20 <= -0.02:
        return (
            "long_put_vertical",
            f"IV={iv_atm:.0%} ≤ 20%, price {price_vs_sma20:+.1%} below SMA20 — cheap debit, directional bearish",
        )

    # Default: short put vertical (neutral/bullish credit spread)
    return (
        "short_put_vertical",
        f"IV={iv_atm:.0%}, price at {price_52w_pct:.0%} of 52w range — standard bull put credit spread",
    )
