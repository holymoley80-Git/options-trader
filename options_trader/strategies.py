"""
Passarelli-based strategy screening rules.
Reference: "Trading Options Greeks" by Dan Passarelli.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class ScreenResult:
    passed: bool
    strategy: str
    reasons: list[str]
    warnings: list[str]


# ---------------------------------------------------------------------------
# Core Greek thresholds (conservative defaults, tune per your risk tolerance)
# ---------------------------------------------------------------------------

# Long vertical spread (debit)
LONG_VERTICAL_RULES = {
    "max_debit_to_spread_width": 0.50,   # pay no more than 50% of width
    "min_delta_long_leg": 0.40,
    "max_delta_long_leg": 0.65,
    "min_open_interest": 100,
}

# Short vertical spread (credit)
SHORT_VERTICAL_RULES = {
    "min_credit_to_spread_width": 0.25,  # collect at least 25% of width
    "max_delta_short_leg": 0.35,         # sell OTM options
    "min_open_interest": 100,
}

# Long calendar spread — sell near, buy far
CALENDAR_RULES = {
    "max_net_debit_to_far_premium": 0.60,
    "iv_rank_min": 20,    # want some IV to sell against
    "iv_rank_max": 60,    # don't pay too much for far leg
    "min_open_interest": 50,
}

# Iron condor
IRON_CONDOR_RULES = {
    "min_credit_to_risk": 0.25,
    "max_call_short_delta": 0.30,
    "max_put_short_delta": -0.30,  # put deltas are negative
    "min_open_interest": 100,
    "iv_rank_min": 30,
}

# Long straddle / strangle
STRADDLE_RULES = {
    "max_iv_rank": 30,      # buy vol when it's cheap
    "near_atm_delta_range": 0.10,  # |delta - 0.50| < this
    "min_open_interest": 100,
}


def screen_long_vertical(
    long_premium: float,
    spread_width: float,
    long_delta: float,
    open_interest: int,
    contract_type: str = "call",
) -> ScreenResult:
    reasons, warnings = [], []
    passed = True

    ratio = long_premium / spread_width if spread_width else 1
    if ratio > LONG_VERTICAL_RULES["max_debit_to_spread_width"]:
        reasons.append(
            f"Debit/width ratio {ratio:.2f} exceeds max "
            f"{LONG_VERTICAL_RULES['max_debit_to_spread_width']}"
        )
        passed = False

    abs_delta = abs(long_delta)
    if not (LONG_VERTICAL_RULES["min_delta_long_leg"] <= abs_delta <= LONG_VERTICAL_RULES["max_delta_long_leg"]):
        warnings.append(
            f"Long leg delta {abs_delta:.2f} outside preferred range "
            f"[{LONG_VERTICAL_RULES['min_delta_long_leg']}, {LONG_VERTICAL_RULES['max_delta_long_leg']}]"
        )

    if open_interest < LONG_VERTICAL_RULES["min_open_interest"]:
        reasons.append(f"Open interest {open_interest} below minimum {LONG_VERTICAL_RULES['min_open_interest']}")
        passed = False

    return ScreenResult(
        passed=passed,
        strategy=f"Long {contract_type} vertical",
        reasons=reasons,
        warnings=warnings,
    )


def screen_short_vertical(
    net_credit: float,
    spread_width: float,
    short_delta: float,
    open_interest: int,
    contract_type: str = "call",
) -> ScreenResult:
    reasons, warnings = [], []
    passed = True

    ratio = net_credit / spread_width if spread_width else 0
    if ratio < SHORT_VERTICAL_RULES["min_credit_to_spread_width"]:
        reasons.append(
            f"Credit/width ratio {ratio:.2f} below minimum "
            f"{SHORT_VERTICAL_RULES['min_credit_to_spread_width']}"
        )
        passed = False

    if abs(short_delta) > SHORT_VERTICAL_RULES["max_delta_short_leg"]:
        warnings.append(
            f"Short leg |delta| {abs(short_delta):.2f} above preferred max "
            f"{SHORT_VERTICAL_RULES['max_delta_short_leg']} — more directional risk"
        )

    if open_interest < SHORT_VERTICAL_RULES["min_open_interest"]:
        reasons.append(f"Open interest {open_interest} below minimum {SHORT_VERTICAL_RULES['min_open_interest']}")
        passed = False

    return ScreenResult(
        passed=passed,
        strategy=f"Short {contract_type} vertical",
        reasons=reasons,
        warnings=warnings,
    )


def screen_iron_condor(
    net_credit: float,
    max_risk: float,
    call_short_delta: float,
    put_short_delta: float,
    open_interest: int,
    iv_rank: float,
) -> ScreenResult:
    reasons, warnings = [], []
    passed = True

    ratio = net_credit / max_risk if max_risk else 0
    if ratio < IRON_CONDOR_RULES["min_credit_to_risk"]:
        reasons.append(
            f"Credit/risk ratio {ratio:.2f} below minimum "
            f"{IRON_CONDOR_RULES['min_credit_to_risk']}"
        )
        passed = False

    if call_short_delta > IRON_CONDOR_RULES["max_call_short_delta"]:
        warnings.append(f"Call short delta {call_short_delta:.2f} is high — wing too close")

    if put_short_delta > IRON_CONDOR_RULES["max_put_short_delta"] * -1:  # put delta is negative
        warnings.append(f"Put short delta {put_short_delta:.2f} is high — wing too close")

    if iv_rank < IRON_CONDOR_RULES["iv_rank_min"]:
        reasons.append(f"IV rank {iv_rank:.0f} below minimum {IRON_CONDOR_RULES['iv_rank_min']} for condor")
        passed = False

    if open_interest < IRON_CONDOR_RULES["min_open_interest"]:
        reasons.append(f"Open interest {open_interest} below minimum {IRON_CONDOR_RULES['min_open_interest']}")
        passed = False

    return ScreenResult(
        passed=passed,
        strategy="Iron condor",
        reasons=reasons,
        warnings=warnings,
    )


def screen_long_straddle(
    atm_delta: float,
    iv_rank: float,
    open_interest: int,
) -> ScreenResult:
    reasons, warnings = [], []
    passed = True

    if iv_rank > STRADDLE_RULES["max_iv_rank"]:
        reasons.append(
            f"IV rank {iv_rank:.0f} exceeds max {STRADDLE_RULES['max_iv_rank']} — buying expensive vol"
        )
        passed = False

    if abs(abs(atm_delta) - 0.50) > STRADDLE_RULES["near_atm_delta_range"]:
        warnings.append(f"ATM leg delta {atm_delta:.2f} deviates from 0.50 — consider re-centering strike")

    if open_interest < STRADDLE_RULES["min_open_interest"]:
        reasons.append(f"Open interest {open_interest} below minimum {STRADDLE_RULES['min_open_interest']}")
        passed = False

    return ScreenResult(
        passed=passed,
        strategy="Long straddle",
        reasons=reasons,
        warnings=warnings,
    )
