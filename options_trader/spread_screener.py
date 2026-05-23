"""
Spread-pair screener — evaluates actual two-leg spreads against Passarelli rules.

Supported spread types:
  short_call_vertical  — bear call credit spread (sell lower call, buy higher call)
  short_put_vertical   — bull put credit spread  (sell higher put,  buy lower put)
  long_call_vertical   — bull call debit spread  (buy lower call,  sell higher call)
  long_put_vertical    — bear put debit spread   (buy higher put,  sell lower put)
"""

from dataclasses import dataclass, field
from typing import Literal

SpreadType = Literal[
    "short_call_vertical",
    "short_put_vertical",
    "long_call_vertical",
    "long_put_vertical",
    "iron_condor",
]


@dataclass
class Spread:
    spread_type: str
    underlying: str
    expiration: str
    short_strike: float
    long_strike: float
    width: float

    # Pricing (per share; multiply by 100 for one contract)
    net_credit: float       # positive = credit received, negative = debit paid
    max_risk: float         # maximum loss per share
    max_profit: float       # maximum gain per share
    break_even: float

    # Greeks (net position: short leg + long leg)
    net_delta: float
    net_gamma: float
    net_theta: float
    net_vega: float

    # Leg data
    short_iv: float
    long_iv: float
    short_oi: int | None
    long_oi: int | None
    short_vol: int | None
    long_vol: int | None

    # Screening output
    credit_to_width: float  # for credit spreads; debit_to_width for debit
    short_delta_abs: float  # |delta| of the short leg
    pop_approx: float       # rough probability of max profit (1 - |short delta|)

    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Screening thresholds
# ---------------------------------------------------------------------------

CREDIT_SPREAD_RULES = {
    "target_credit_to_width": 0.20,  # warn below this; not a hard fail (PoP governs OTM-ness)
    "max_short_delta": 0.45,         # hard fail — short leg must be at most near-ATM
    "preferred_short_delta": 0.35,   # warn above this
    "min_oi_per_leg": 50,
    "min_credit": 0.10,              # hard fail — absolute floor to avoid penny spreads
}

DEBIT_SPREAD_RULES = {
    "max_debit_to_width": 0.55,      # pay no more than 55% of width
    "min_long_delta": 0.35,
    "max_long_delta": 0.70,
    "min_oi_per_leg": 50,
}

IRON_CONDOR_RULES = {
    "max_short_delta_per_side": 0.30,   # each short leg must be OTM
    "min_credit_to_width": 0.25,        # combined credit / wing width
    "min_credit": 0.20,                 # absolute floor
    "min_oi_per_leg": 50,
}


@dataclass
class IronCondor:
    underlying: str
    expiration: str
    # Put spread (lower strikes)
    short_put: float
    long_put: float
    put_width: float
    put_credit: float
    short_put_delta: float
    # Call spread (upper strikes)
    short_call: float
    long_call: float
    call_width: float
    call_credit: float
    short_call_delta: float
    # Combined metrics
    net_credit: float
    width: float
    credit_to_width: float
    max_risk: float
    pop_approx: float       # ≈ 1 - |short_put_delta| - |short_call_delta|
    net_delta: float
    net_gamma: float
    net_theta: float
    net_vega: float
    short_iv: float         # average IV of the two short legs
    # OI for liquidity checks
    short_put_oi: int | None = None
    long_put_oi: int | None = None
    short_call_oi: int | None = None
    long_call_oi: int | None = None
    spread_type: str = "iron_condor"
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------

def build_spread_pairs(
    chain: list[dict],
    spread_type: SpreadType,
    widths: list[float],
) -> list[Spread]:
    """
    Given a flat options chain, generate all valid spread pairs for the
    requested spread type and width(s).

    Uses natural pricing: sell at bid, buy at ask.
    """
    ctype = "call" if "call" in spread_type else "put"
    legs = {
        c["strike"]: c
        for c in chain
        if c.get("contract_type") == ctype
           and c.get("bid") is not None
           and c.get("ask") is not None
           and c.get("delta") is not None
    }
    strikes = sorted(legs.keys())
    spreads: list[Spread] = []

    for width in widths:
        for s in strikes:
            partner = s + width
            if partner not in legs:
                continue

            low = legs[s]       # lower strike
            high = legs[partner]  # higher strike

            if "short_call" in spread_type:
                # Sell lower call, buy higher call
                short, long_ = low, high
            elif "short_put" in spread_type:
                # Sell higher put, buy lower put
                short, long_ = high, low
            elif "long_call" in spread_type:
                # Buy lower call, sell higher call
                long_, short = low, high
            else:  # long_put
                # Buy higher put, sell lower put
                long_, short = high, low

            short_bid = short["bid"]
            long_ask = long_["ask"]
            net_credit = short_bid - long_ask  # negative means net debit

            is_credit = "short_" in spread_type
            if is_credit:
                max_risk = width - net_credit
                max_profit = net_credit
                if "call" in spread_type:
                    break_even = short["strike"] + net_credit
                else:
                    break_even = short["strike"] - net_credit
                ratio = net_credit / width if width else 0
            else:
                net_debit = -net_credit  # flip sign for readability
                max_risk = net_debit
                max_profit = width - net_debit
                if "call" in spread_type:
                    break_even = long_["strike"] + net_debit
                else:
                    break_even = long_["strike"] - net_debit
                ratio = net_debit / width if width else 1

            # Position Greeks: we are SHORT the short leg and LONG the long leg,
            # so the short leg's Greeks are negated before summing.
            def _g(leg, key):
                return leg.get(key) or 0

            spread = Spread(
                spread_type=spread_type,
                underlying=short["underlying"],
                expiration=short["expiration"],
                short_strike=short["strike"],
                long_strike=long_["strike"],
                width=width,
                net_credit=round(net_credit, 2),
                max_risk=round(max(max_risk, 0), 2),
                max_profit=round(max(max_profit, 0), 2),
                break_even=round(break_even, 2),
                net_delta=round(-_g(short, "delta") + _g(long_, "delta"), 4),
                net_gamma=round(-_g(short, "gamma") + _g(long_, "gamma"), 4),
                net_theta=round(-_g(short, "theta") + _g(long_, "theta"), 4),
                net_vega=round( -_g(short, "vega")  + _g(long_, "vega"),  4),
                short_iv=short.get("iv") or 0,
                long_iv=long_.get("iv") or 0,
                short_oi=short.get("open_interest"),
                long_oi=long_.get("open_interest"),
                short_vol=short.get("volume"),
                long_vol=long_.get("volume"),
                credit_to_width=round(ratio, 3),
                short_delta_abs=round(abs(short.get("delta") or 0), 3),
                pop_approx=round(1 - abs(short.get("delta") or 0), 3),
            )
            spreads.append(spread)

    return spreads


# ---------------------------------------------------------------------------
# Screener
# ---------------------------------------------------------------------------

def screen_spreads(
    spreads: list[Spread],
    spread_type: SpreadType,
    min_pop: float = 0.0,
) -> list[Spread]:
    """
    Apply Passarelli-based rules. Returns only spreads that pass hard filters,
    with warnings attached for soft violations. Sorted by best ratio first.
    """
    passed = []
    is_credit = "short_" in spread_type
    rules = CREDIT_SPREAD_RULES if is_credit else DEBIT_SPREAD_RULES

    for sp in spreads:
        warnings: list[str] = []
        fail = False

        if sp.pop_approx < min_pop:
            fail = True

        if is_credit:
            if sp.net_credit < rules["min_credit"]:
                fail = True
            if sp.short_delta_abs > rules["max_short_delta"]:
                fail = True
            min_oi = rules["min_oi_per_leg"]
            if (sp.short_oi or 0) < min_oi or (sp.long_oi or 0) < min_oi:
                fail = True
            if sp.credit_to_width < rules["target_credit_to_width"]:
                warnings.append(
                    f"Cr/width {sp.credit_to_width:.0%} below target "
                    f"{rules['target_credit_to_width']:.0%} — thin premium"
                )
            if sp.short_delta_abs > rules["preferred_short_delta"]:
                warnings.append(
                    f"Short delta {sp.short_delta_abs:.2f} > preferred {rules['preferred_short_delta']} "
                    f"— more directional"
                )
        else:
            net_debit = -sp.net_credit
            if sp.credit_to_width > rules["max_debit_to_width"]:
                fail = True
            long_d = abs(sp.net_delta) + sp.short_delta_abs  # reconstruct long delta approx
            if not (rules["min_long_delta"] <= sp.short_delta_abs <= rules["max_long_delta"]):
                warnings.append(
                    f"Long leg delta {sp.short_delta_abs:.2f} outside preferred range "
                    f"[{rules['min_long_delta']}, {rules['max_long_delta']}]"
                )
            min_oi = rules["min_oi_per_leg"]
            if (sp.short_oi or 0) < min_oi or (sp.long_oi or 0) < min_oi:
                fail = True

        if not fail:
            sp.warnings = warnings
            passed.append(sp)

    key = (lambda s: -s.credit_to_width) if is_credit else (lambda s: s.credit_to_width)
    return sorted(passed, key=key)


# ---------------------------------------------------------------------------
# Iron condor builder + screener
# ---------------------------------------------------------------------------

def build_iron_condors(chain: list[dict], widths: list[float]) -> list[IronCondor]:
    """
    Combine the best short put vertical and short call vertical into iron condors.
    Filters each side to short delta 0.10–0.30 before pairing.
    """
    put_pairs = build_spread_pairs(chain, "short_put_vertical", widths)
    call_pairs = build_spread_pairs(chain, "short_call_vertical", widths)

    # Keep OTM candidates only, best credit first; limit to avoid O(n²) blow-up
    put_cands = sorted(
        [p for p in put_pairs if 0.10 <= p.short_delta_abs <= 0.30],
        key=lambda s: -s.net_credit,
    )[:6]
    call_cands = sorted(
        [c for c in call_pairs if 0.10 <= c.short_delta_abs <= 0.30],
        key=lambda s: -s.net_credit,
    )[:6]

    condors: list[IronCondor] = []
    for put in put_cands:
        for call in call_cands:
            if call.expiration != put.expiration:
                continue
            if call.width != put.width:
                continue
            if put.short_strike >= call.short_strike:
                continue  # spreads must not overlap

            net_credit = round(put.net_credit + call.net_credit, 2)
            width = put.width
            pop = round(max(0.0, 1.0 - put.short_delta_abs - call.short_delta_abs), 3)

            warnings: list[str] = []
            if abs(put.short_delta_abs - call.short_delta_abs) > 0.08:
                warnings.append(
                    f"asymmetric Δ: put {put.short_delta_abs:.2f} / call {call.short_delta_abs:.2f}"
                )

            condors.append(IronCondor(
                underlying=put.underlying,
                expiration=put.expiration,
                short_put=put.short_strike,
                long_put=put.long_strike,
                put_width=put.width,
                put_credit=put.net_credit,
                short_put_delta=put.short_delta_abs,
                short_call=call.short_strike,
                long_call=call.long_strike,
                call_width=call.width,
                call_credit=call.net_credit,
                short_call_delta=call.short_delta_abs,
                net_credit=net_credit,
                width=width,
                credit_to_width=round(net_credit / width, 3) if width else 0,
                max_risk=round(width - net_credit, 2),
                pop_approx=pop,
                net_delta=round(put.net_delta + call.net_delta, 4),
                net_gamma=round(put.net_gamma + call.net_gamma, 4),
                net_theta=round(put.net_theta + call.net_theta, 4),
                net_vega=round(put.net_vega + call.net_vega, 4),
                short_iv=round((put.short_iv + call.short_iv) / 2, 4),
                short_put_oi=put.short_oi,
                long_put_oi=put.long_oi,
                short_call_oi=call.short_oi,
                long_call_oi=call.long_oi,
                warnings=warnings,
            ))

    return condors


def screen_iron_condors(
    condors: list[IronCondor],
    min_pop: float = 0.50,
) -> list[IronCondor]:
    """Apply hard filters to iron condors; return sorted by credit/width descending."""
    rules = IRON_CONDOR_RULES
    passed: list[IronCondor] = []

    for ic in condors:
        if ic.net_credit < rules["min_credit"]:
            continue
        if ic.short_put_delta > rules["max_short_delta_per_side"]:
            continue
        if ic.short_call_delta > rules["max_short_delta_per_side"]:
            continue
        if ic.pop_approx < min_pop:
            continue
        min_oi = rules["min_oi_per_leg"]
        if any((oi or 0) < min_oi for oi in [
            ic.short_put_oi, ic.long_put_oi, ic.short_call_oi, ic.long_call_oi
        ]):
            continue
        if ic.credit_to_width < rules["min_credit_to_width"]:
            ic.warnings.append(
                f"Cr/width {ic.credit_to_width:.0%} below target {rules['min_credit_to_width']:.0%}"
            )
        passed.append(ic)

    return sorted(passed, key=lambda c: -c.credit_to_width)
