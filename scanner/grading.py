"""Grades pending simulated bets against real DataGolf results.

Uses two endpoints beyond the odds feeds:
  - /get-schedule            -- event status (completed?) and winner
  - /historical-raw-data/rounds -- per-player finish text + per-round scores

Grading rules are a documented approximation, not exact bookmaker
settlement logic (real books have edge-case tie/void rules that vary by
book and aren't published anywhere DataGolf's API exposes):
  - WIN/TOP_5/TOP_10/TOP_20: graded on final finish position.
  - MC: graded on whether the player has a round 3 score on record.
  - FRL: graded on round 1 score; a tie for the lead is a push.
  - TOURNAMENT_MATCHUPS: better final position wins; a player who made the
    cut beats one who didn't; if neither finished, it's voided.
  - ROUND_MATCHUPS / 3_BALLS: graded on the specific round's score (using
    round_num captured at pick time); a tie is a push.

FRL, MC, ROUND_MATCHUPS, and 3_BALLS don't need the whole tournament to
finish -- FRL only needs round 1 done, MC only needs the cut made, a round
matchup only needs that specific round done. A third endpoint,
/preds/in-play, exposes exactly that live, in-progress state (per-player
round scores as they're completed, plus a settled make_cut flag), so those
four markets get graded as soon as they're actually decided rather than
waiting for the full event to show "completed" in /get-schedule.
"""

from __future__ import annotations

import json

import simulator_db
from datagolf_client import DataGolfClient

CUT_LIKE = {"CUT", "WD", "DQ", "MDF"}


def _parse_rank(fin_text: str | None) -> float | None:
    if not fin_text:
        return None
    t = fin_text.strip().upper()
    if t in CUT_LIKE:
        return None
    t = t.lstrip("T")
    try:
        return float(t)
    except ValueError:
        return None


def _made_cut(entry: dict) -> bool | None:
    fin_text = (entry.get("fin_text") or "").strip().upper()
    if fin_text == "CUT":
        return False
    if entry.get("round_3") is not None:
        return True
    if fin_text in ("WD", "DQ"):
        return False
    return None


def _round_score(entry: dict | None, round_num: int) -> float | None:
    if not entry:
        return None
    r = entry.get(f"round_{round_num}")
    if not isinstance(r, dict):
        return None
    score = r.get("score")
    return float(score) if isinstance(score, (int, float)) else None


def _build_results_index(rounds_data: dict) -> dict[str, dict]:
    index: dict[str, dict] = {}
    for entry in rounds_data.get("scores", []) or []:
        name = entry.get("player_name")
        if name:
            index[name] = entry
    return index


def _grade_one(bet, results: dict[str, dict]) -> tuple[str, float, str]:
    """Returns (status, payout, note). payout is profit/loss in dollars."""
    stake = bet["stake"]
    decimal = bet["book_decimal"]
    player = bet["player_name"]
    market = bet["market"].upper()
    entry = results.get(player)

    def win():
        return "won", round(stake * (decimal - 1), 2), ""

    def lose():
        return "lost", -stake, ""

    def push():
        return "push", 0.0, ""

    def void(note):
        return "void", 0.0, note

    if entry is None:
        return void("player not found in results (WD before playing, or a name mismatch)")

    if market in ("WIN", "TOP_5", "TOP_10", "TOP_20"):
        threshold = {"WIN": 1, "TOP_5": 5, "TOP_10": 10, "TOP_20": 20}[market]
        rank = _parse_rank(entry.get("fin_text"))
        if rank is None:
            return lose()
        return win() if rank <= threshold else lose()

    if market == "MC":
        made = _made_cut(entry)
        if made is None:
            return void("ambiguous cut status in results data")
        return win() if made else lose()

    if market == "FRL":
        r1 = _round_score(entry, 1)
        if r1 is None:
            return void("no round 1 score on record")
        field_r1 = [s for s in (_round_score(e, 1) for e in results.values()) if s is not None]
        if not field_r1:
            return void("no round 1 field data")
        best = min(field_r1)
        if r1 > best:
            return lose()
        leaders = sum(1 for s in field_r1 if s == best)
        return push() if leaders > 1 else win()

    if market == "TOURNAMENT_MATCHUPS":
        opponents = json.loads(bet["opponents"])
        my_rank = _parse_rank(entry.get("fin_text"))
        opp_ranks = [
            _parse_rank(results[opp].get("fin_text")) if opp in results else None
            for opp in opponents
        ]
        my_sort = my_rank if my_rank is not None else float("inf")
        opp_sorts = [r if r is not None else float("inf") for r in opp_ranks]
        if my_sort == float("inf") and all(o == float("inf") for o in opp_sorts):
            return void("everyone in this matchup missed the cut/withdrew -- voided")
        best_opp = min(opp_sorts) if opp_sorts else float("inf")
        if my_sort < best_opp:
            return win()
        if my_sort > best_opp:
            return lose()
        return push()

    if market in ("ROUND_MATCHUPS", "3_BALLS"):
        round_num = bet["round_num"]
        if round_num is None:
            return void("round number unknown, can't grade a round-specific market")
        opponents = json.loads(bet["opponents"])
        my_score = _round_score(entry, round_num)
        opp_scores = [
            _round_score(results.get(opp), round_num) for opp in opponents
        ]
        valid_opp = [s for s in opp_scores if s is not None]
        if my_score is None and not valid_opp:
            return void("neither side has a score for this round")
        if my_score is None:
            return lose()
        if not valid_opp:
            return win()
        best_opp = min(valid_opp)
        if my_score < best_opp:
            return win()
        if my_score > best_opp:
            return lose()
        return push()

    return void(f"no grading rule for market {market}")


INPLAY_GRADABLE_MARKETS = {"FRL", "MC", "ROUND_MATCHUPS", "3_BALLS"}
FIELD_ROUND_COMPLETE_THRESHOLD = 0.95  # tolerate a few laggard groups


def _inplay_round_score(entry: dict, round_num: int) -> float | None:
    """A player's score for round_num from the live feed, but only once
    that round is actually *finished* for them -- not a live-updating
    partial score from a round still in progress."""
    score = entry.get(f"R{round_num}")
    if not isinstance(score, (int, float)):
        return None
    current_round = entry.get("round")
    if not isinstance(current_round, (int, float)):
        return None
    if current_round > round_num:
        return float(score)
    if current_round == round_num and entry.get("thru") == 18:
        return float(score)
    return None


def _build_inplay_index(data: dict) -> dict[str, dict]:
    index: dict[str, dict] = {}
    for entry in (data or {}).get("data", []) or []:
        name = entry.get("player_name")
        if name:
            index[name] = entry
    return index


def _grade_one_inplay(bet, inplay_index: dict[str, dict]) -> tuple[str, float, str] | None:
    """Grade FRL/MC/round-matchup/3-ball bets from the live in-play feed.
    Returns None (stay pending) when the relevant round/cut isn't actually
    decided yet -- never guesses from an in-progress score."""
    stake = bet["stake"]
    decimal = bet["book_decimal"]
    player = bet["player_name"]
    market = bet["market"].upper()
    entry = inplay_index.get(player)

    def win():
        return "won", round(stake * (decimal - 1), 2), "graded live (in-play, pre-final)"

    def lose():
        return "lost", -stake, "graded live (in-play, pre-final)"

    def push():
        return "push", 0.0, "graded live (in-play, pre-final)"

    if market == "FRL":
        if entry is None:
            return None
        r1 = _inplay_round_score(entry, 1)
        if r1 is None:
            return None
        field_r1 = [s for s in (_inplay_round_score(e, 1) for e in inplay_index.values()) if s is not None]
        if not field_r1 or len(field_r1) < len(inplay_index) * FIELD_ROUND_COMPLETE_THRESHOLD:
            return None  # round 1 not functionally over for the field yet
        best = min(field_r1)
        if r1 > best:
            return lose()
        leaders = sum(1 for s in field_r1 if s == best)
        return push() if leaders > 1 else win()

    if market == "MC":
        if entry is None:
            return None
        mc = entry.get("make_cut")
        if mc == 1.0:
            return win()
        if mc == 0.0:
            return lose()
        return None  # still a probability, not yet settled

    if market in ("ROUND_MATCHUPS", "3_BALLS"):
        round_num = bet["round_num"]
        if round_num is None:
            return None
        opponents = json.loads(bet["opponents"])
        my_score = _inplay_round_score(entry, round_num) if entry else None
        opp_scores = [
            _inplay_round_score(inplay_index[opp], round_num) if opp in inplay_index else None
            for opp in opponents
        ]
        if my_score is None or any(s is None for s in opp_scores):
            return None  # wait until everyone in the group has finished this round
        best_opp = min(opp_scores)
        if my_score < best_opp:
            return win()
        if my_score > best_opp:
            return lose()
        return push()

    return None


def _event_names_match(bet_event_name: str, tour: str, sched_event_name: str) -> bool:
    """DataGolf names LIV events inconsistently across its own endpoints --
    the betting-tools odds payload calls one "LIV New York" while
    get-schedule calls the same event just "New York". Strip that prefix
    when comparing on the alt/LIV tour so grading doesn't silently stall."""
    if bet_event_name == sched_event_name:
        return True
    if tour == "alt" and bet_event_name.lower().startswith("liv "):
        return bet_event_name[4:].strip().lower() == sched_event_name.strip().lower()
    return False


def grade_pending(client: DataGolfClient, db_path: str = simulator_db.DB_PATH) -> dict:
    """One grading pass: check pending events, grade any now completed."""
    summary = {"events_checked": 0, "events_graded": 0, "bets_graded": 0}
    pending = simulator_db.pending_events(db_path)
    if not pending:
        return summary

    tours_needed = sorted({t for _, t in pending})
    schedules: dict[str, list[dict]] = {}
    inplay_by_tour: dict[str, dict[str, dict]] = {}
    for tour in tours_needed:
        data = client.schedule(tour)
        schedules[tour] = (data or {}).get("schedule", []) if isinstance(data, dict) else []
        inplay_data = client.in_play(tour)
        inplay_by_tour[tour] = _build_inplay_index(inplay_data) if isinstance(inplay_data, dict) else {}

    for event_name, tour in pending:
        summary["events_checked"] += 1

        # Grade whatever's already decided (FRL/MC/round matchups) from the
        # live feed, regardless of whether the whole event is done yet.
        inplay_index = inplay_by_tour.get(tour)
        if inplay_index:
            for bet in simulator_db.pending_bets_for_event(event_name, tour, db_path):
                if bet["market"].upper() not in INPLAY_GRADABLE_MARKETS:
                    continue
                result = _grade_one_inplay(bet, inplay_index)
                if result is None:
                    continue
                status, payout, note = result
                simulator_db.apply_grade(bet["id"], status, payout, note, db_path)
                summary["bets_graded"] += 1

        sched_entry = next(
            (e for e in schedules.get(tour, []) if _event_names_match(event_name, tour, e.get("event_name", ""))),
            None,
        )
        if not sched_entry or sched_entry.get("status") != "completed":
            continue

        event_id = sched_entry.get("event_id")
        start_date = sched_entry.get("start_date", "")
        year = start_date[:4] if start_date else None
        if not event_id or not year:
            continue

        rounds_data = client.historical_rounds(tour, event_id, int(year))
        if not isinstance(rounds_data, dict) or "scores" not in rounds_data:
            continue

        results = _build_results_index(rounds_data)
        bets = simulator_db.pending_bets_for_event(event_name, tour, db_path)
        for bet in bets:
            status, payout, note = _grade_one(bet, results)
            simulator_db.apply_grade(bet["id"], status, payout, note, db_path)
            summary["bets_graded"] += 1

        simulator_db.mark_event_graded(event_name, tour, db_path)
        summary["events_graded"] += 1

    return summary
