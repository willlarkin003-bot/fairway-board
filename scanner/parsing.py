"""Adaptive normalizer for DataGolf betting-tools JSON.

DataGolf doesn't publish a strict machine-readable schema, and their field
naming has changed between endpoints/versions in the past (this is the #1
reason a scanner ends up parsing "0 rows" against a live event). Rather than
hardcoding one exact shape, this module:

  1. Finds the list of row records wherever it lives in the payload
     (top-level list, or nested under "odds"/"match_list"/"pairings"/etc).
  2. For matchup rows (tournament matchups, round matchups, 3-balls), finds
     the per-player sub-records wherever they live (p1_/p2_/p3_-prefixed
     keys, or a nested list of player dicts).
  3. Looks up the book price and model figure by trying a list of known
     key aliases, case-insensitively.
  4. Accepts the model figure as either a probability (0-1) or decimal odds
     (>1) and normalizes it to a probability.

If your DataGolf response uses field names not covered here, run
`python app.py --diagnose` — it dumps the raw keys seen on a sample row so
the alias lists below can be extended.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import config

NAME_KEYS = ["player_name", "name", "golfer", "golfer_name"]
ID_KEYS = ["dg_id", "player_id", "id"]
BOOK_KEYS = [config.BOOK, config.BOOK.replace("365", "_365"), config.BOOK.upper()]
MODEL_KEYS = ["datagolf", "dg_odds", "dg", "model", "baseline", "fair_odds"]
MODEL_SUBKEYS = ["baseline_history_fit", "baseline", "fair_odds", "odds", "prob", "probability"]

_PLAYER_PREFIX_RE = re.compile(r"^p(\d+)_")


@dataclass
class BetRecord:
    tour: str
    market: str
    event_name: str
    player_name: str
    dg_id: object
    opponents: list[str]
    book_decimal: float
    model_prob: float
    round_num: int | None = None


def _find_key(d: dict, candidates: list[str]):
    lower_map = {k.lower(): k for k in d.keys()}
    for cand in candidates:
        if cand.lower() in lower_map:
            return lower_map[cand.lower()]
    return None


def _find_val(d: dict, candidates: list[str]):
    key = _find_key(d, candidates)
    return d.get(key) if key is not None else None


def _to_decimal_odds(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, dict):
        for sub in MODEL_SUBKEYS:
            v = _find_val(value, [sub])
            if v is not None:
                return _to_decimal_odds(v)
        return None
    if isinstance(value, str):
        try:
            value = float(value)
        except ValueError:
            return None
    if isinstance(value, (int, float)):
        if value > 1.0:
            return float(value)
    return None


def _to_probability(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, dict):
        for sub in MODEL_SUBKEYS:
            v = _find_val(value, [sub])
            if v is not None:
                return _to_probability(v)
        return None
    if isinstance(value, str):
        try:
            value = float(value.rstrip("%"))
        except ValueError:
            return None
    if not isinstance(value, (int, float)):
        return None
    v = float(value)
    if 0.0 < v <= 1.0:
        # already a bare probability (0-1 fraction)
        return v
    if v > 1.0:
        # we always request odds_format=decimal, so DataGolf's own model
        # column is decimal odds too, same as every sportsbook column
        return 1.0 / v
    return None


def _extract_rows(data) -> tuple[list[dict], str, int | None]:
    """Return (row_list, event_name, round_num) from a raw API payload.

    round_num is only present on round-specific matchup markets (round
    matchups, 3-balls) and tells grading.py which round's score to compare.
    """
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)], "", None

    if not isinstance(data, dict):
        return [], "", None

    event_name = data.get("event_name", "") or ""
    round_num = data.get("round_num")
    round_num = int(round_num) if isinstance(round_num, (int, float)) else None

    for key in ("odds", "match_list", "pairings", "data"):
        val = data.get(key)
        if isinstance(val, list):
            return [r for r in val if isinstance(r, dict)], event_name, round_num

    for val in data.values():
        if isinstance(val, list) and val and isinstance(val[0], dict):
            return [r for r in val if isinstance(r, dict)], event_name, round_num

    return [], event_name, round_num


def _player_slots(row: dict) -> list[dict]:
    """Split one row into a list of per-player dicts, however it's shaped."""
    prefixes = sorted({m.group(0) for k in row.keys() if (m := _PLAYER_PREFIX_RE.match(k))})
    odds = row.get("odds")

    # Case 1: matchups/3-balls as actually returned by DataGolf -- identity
    # fields are p1_/p2_/p3_-prefixed, but the prices live in a separate
    # nested "odds" dict keyed by sportsbook, each mapping "p1"/"p2"/"p3" (and
    # sometimes "tie") to a decimal price, e.g.:
    #   {"p1_player_name": ..., "p2_player_name": ...,
    #    "odds": {"bet365": {"p1": 2.0, "p2": 1.83, "tie": 17.0},
    #             "datagolf": {"p1": 2.17, "p2": 2.04, "tie": 20.8}}}
    if prefixes and isinstance(odds, dict):
        book_odds = _find_val(odds, BOOK_KEYS)
        model_odds = _find_val(odds, MODEL_KEYS)
        slots = []
        for pfx in prefixes:
            label = pfx.rstrip("_")  # "p1_" -> "p1", matches the odds sub-dict key
            slot = {k[len(pfx):]: v for k, v in row.items() if k.startswith(pfx)}
            if isinstance(book_odds, dict):
                slot[config.BOOK] = book_odds.get(label)
            if isinstance(model_odds, dict):
                slot["datagolf"] = model_odds.get(label)
            slots.append(slot)
        return slots

    # Case 2: nested list of player dicts.
    for val in row.values():
        if isinstance(val, list) and val and isinstance(val[0], dict):
            return val

    # Case 3: p1_/p2_/p3_-prefixed flat keys with no separate "odds" dict
    # (book/model directly on each prefix, e.g. "p1_bet365").
    if prefixes:
        slots = []
        for pfx in prefixes:
            slot = {k[len(pfx):]: v for k, v in row.items() if k.startswith(pfx)}
            slots.append(slot)
        return slots

    # Case 3: the row already is a single player's record (outrights).
    return [row]


def parse(data, tour: str, market: str) -> list[BetRecord]:
    rows, event_name, round_num = _extract_rows(data)
    records: list[BetRecord] = []

    for row in rows:
        slots = _player_slots(row)
        names = [_find_val(s, NAME_KEYS) for s in slots]

        for i, slot in enumerate(slots):
            name = names[i]
            if not name:
                continue
            book_dec = _to_decimal_odds(_find_val(slot, BOOK_KEYS))
            model_prob = _to_probability(_find_val(slot, MODEL_KEYS))
            if book_dec is None or model_prob is None:
                continue
            opponents = [n for j, n in enumerate(names) if j != i and n]
            records.append(
                BetRecord(
                    tour=tour,
                    market=market,
                    event_name=event_name,
                    player_name=name,
                    dg_id=_find_val(slot, ID_KEYS),
                    opponents=opponents,
                    book_decimal=book_dec,
                    model_prob=model_prob,
                    round_num=round_num,
                )
            )

    return records


def sample_keys(data) -> dict:
    """For --diagnose: show the raw keys seen on one sample row/slot."""
    rows, event_name, round_num = _extract_rows(data)
    if not rows:
        return {"event_name": event_name, "round_num": round_num, "row_keys": [], "slot_keys": []}
    slots = _player_slots(rows[0])
    return {
        "event_name": event_name,
        "round_num": round_num,
        "row_keys": sorted(rows[0].keys()),
        "slot_keys": sorted(slots[0].keys()) if slots else [],
    }
