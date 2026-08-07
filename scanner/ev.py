"""EV and fractional-Kelly staking math.

DataGolf's model probability is already de-vigged (it's their own fair
prediction, not derived from a book line), so EV is simply the standard
edge formula against the sportsbook's decimal price.
"""

from __future__ import annotations

from dataclasses import dataclass

import config


@dataclass
class EvResult:
    fair_decimal_odds: float
    ev_percent: float
    kelly_stake: float


def to_american(decimal_odds: float) -> str:
    if decimal_odds >= 2.0:
        american = (decimal_odds - 1.0) * 100.0
        return f"+{round(american)}"
    american = -100.0 / (decimal_odds - 1.0)
    return f"{round(american)}"


def fair_decimal_odds(model_prob: float) -> float:
    if model_prob <= 0:
        return float("inf")
    return 1.0 / model_prob


def ev_percent(model_prob: float, book_decimal_odds: float) -> float:
    return (model_prob * book_decimal_odds - 1.0) * 100.0


def kelly_fraction(model_prob: float, book_decimal_odds: float) -> float:
    """Full Kelly fraction of bankroll. b = net decimal odds."""
    b = book_decimal_odds - 1.0
    if b <= 0:
        return 0.0
    q = 1.0 - model_prob
    f = (b * model_prob - q) / b
    return max(0.0, f)


def evaluate(model_prob: float, book_decimal_odds: float) -> EvResult:
    ev_pct = ev_percent(model_prob, book_decimal_odds)
    f_full = kelly_fraction(model_prob, book_decimal_odds)
    stake = config.BANKROLL * f_full * config.KELLY_FRACTION
    stake = min(stake, config.MAX_STAKE)
    stake = round(stake, 2)
    return EvResult(
        fair_decimal_odds=round(fair_decimal_odds(model_prob), 2),
        ev_percent=round(ev_pct, 2),
        kelly_stake=stake,
    )
