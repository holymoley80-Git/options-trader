"""Black-Scholes Greeks calculations."""

import math
from dataclasses import dataclass
from scipy.stats import norm


@dataclass
class Greeks:
    delta: float
    gamma: float
    theta: float  # per day
    vega: float   # per 1% move in IV
    rho: float
    iv: float
    intrinsic: float
    extrinsic: float


def _d1(S: float, K: float, T: float, r: float, sigma: float) -> float:
    return (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))


def _d2(d1: float, sigma: float, T: float) -> float:
    return d1 - sigma * math.sqrt(T)


def calculate_greeks(
    S: float,       # underlying price
    K: float,       # strike price
    T: float,       # time to expiration in years
    r: float,       # risk-free rate (e.g. 0.05)
    sigma: float,   # implied volatility (e.g. 0.30)
    option_type: str = "call",  # "call" or "put"
) -> Greeks:
    """Calculate Black-Scholes Greeks for a single option."""
    if T <= 0:
        raise ValueError("Time to expiration must be positive")
    if sigma <= 0:
        raise ValueError("Implied volatility must be positive")

    d1 = _d1(S, K, T, r, sigma)
    d2 = _d2(d1, sigma, T)
    sqrt_T = math.sqrt(T)
    pdf_d1 = norm.pdf(d1)

    is_call = option_type.lower() == "call"

    if is_call:
        delta = norm.cdf(d1)
        price = S * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)
        intrinsic = max(S - K, 0.0)
        rho = K * T * math.exp(-r * T) * norm.cdf(d2) / 100
    else:
        delta = norm.cdf(d1) - 1
        price = K * math.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)
        intrinsic = max(K - S, 0.0)
        rho = -K * T * math.exp(-r * T) * norm.cdf(-d2) / 100

    gamma = pdf_d1 / (S * sigma * sqrt_T)
    # Theta expressed per calendar day
    theta = (
        -(S * pdf_d1 * sigma) / (2 * sqrt_T)
        - r * K * math.exp(-r * T) * (norm.cdf(d2) if is_call else norm.cdf(-d2))
    ) / 365
    vega = S * pdf_d1 * sqrt_T / 100  # per 1% change in vol
    extrinsic = max(price - intrinsic, 0.0)

    return Greeks(
        delta=round(delta, 4),
        gamma=round(gamma, 4),
        theta=round(theta, 4),
        vega=round(vega, 4),
        rho=round(rho, 4),
        iv=sigma,
        intrinsic=round(intrinsic, 4),
        extrinsic=round(extrinsic, 4),
    )
