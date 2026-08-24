"""Split-cadence scanning: outrights/top-N refresh fast, matchups/mc/frl
(plus grading, which needs its own extra requests) refresh slower.

The literal ask that motivated this ("outrights every 10s, matchups every
60s") adds up to 105 requests/minute -- more than double DataGolf's 45/min
cap -- so it's not what this does. Instead:

  FAST  group: win, top_5, top_10, top_20 x 4 tours = 16 requests,
        re-run roughly every 30s (~32 req/min).
  SLOW  group: mc, frl x 4 tours + matchups x 3 tours = 17 requests, plus
        grading's own schedule()/in_play() checks (~8 more), re-run
        roughly every 150s (~10 req/min).

Combined that's roughly 42 req/min *by design intent* -- but the number
that actually matters is DataGolfClient's own pacing (60/REQUESTS_PER_MINUTE
seconds between *any* two requests, currently a 1.5s floor at 40/min),
which is what actually, unconditionally enforces the cap. That pacing
timestamp is persisted to disk here specifically so it survives across
separate process invocations -- required for the cloud automation, which
calls `python app.py --tick` fresh each time rather than running one
long-lived process.

Usage:
    python app.py --tick          do whatever's due, once, then exit
                                   (what the cloud automation calls
                                   repeatedly in its own loop)
    python app.py --watch-fast    persistent local loop calling --tick
                                   every few seconds
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime

import app as app_module
import config
import dashboard
import discord_alert
import parsing
from datagolf_client import DataGolfClient, RateLimitSuspended

STATE_PATH = "cadence_state.json"

FAST_MARKETS = ("win", "top_5", "top_10", "top_20")
FAST_INTERVAL_SECONDS = 30
SLOW_INTERVAL_SECONDS = 150


def _fast_tasks() -> list[tuple[str, str, str]]:
    return [("outrights", tour, m) for tour in config.TOURS for m in FAST_MARKETS]


def _slow_tasks() -> list[tuple[str, str, str]]:
    tasks = [("outrights", tour, m) for tour in config.TOURS for m in ("mc", "frl")]
    for tour in config.TOURS:
        if tour not in config.MATCHUP_SUPPORTED_TOURS:
            continue
        for m in config.MATCHUP_MARKETS:
            tasks.append(("matchups", tour, m))
    return tasks


@dataclass
class CadenceState:
    next_fast_due: float = 0.0
    next_slow_due: float = 0.0
    last_request_ts: float = 0.0
    fast_bets: list = field(default_factory=list)
    slow_bets: list = field(default_factory=list)
    completed_by_tour: dict = field(default_factory=dict)
    previous_keys: list = field(default_factory=list)  # [[market, tour, selection], ...] or None-marker


def _load_state(path: str) -> CadenceState:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        known = {k: data[k] for k in CadenceState.__dataclass_fields__ if k in data}
        return CadenceState(**known)
    except (FileNotFoundError, ValueError, TypeError, json.JSONDecodeError):
        return CadenceState()


def _save_state(state: CadenceState, path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(asdict(state), f)


def _run_tasks(client: DataGolfClient, tasks: list[tuple[str, str, str]]) -> list[parsing.BetRecord]:
    records: list[parsing.BetRecord] = []
    i = 0
    while i < len(tasks):
        kind, tour, market = tasks[i]
        try:
            data = client.outrights(tour, market) if kind == "outrights" else client.matchups(tour, market)
        except RateLimitSuspended as exc:
            wait_s = exc.args[0] if exc.args else 300.0
            print(f"  Rate limited by DataGolf, waiting {int(wait_s)}s before resuming...")
            time.sleep(wait_s)
            continue
        if data is not None:
            records.extend(parsing.parse(data, tour, market))
        i += 1
    return records


def tick(min_ev: float | None = None, state_path: str = STATE_PATH) -> dict:
    """Run whichever bucket(s) are due, merge with the other bucket's last
    known results, and re-render the board (always) and simulator (on slow
    ticks, since grading's extra requests are budgeted into the slow
    cadence). Calling this when nothing is due yet does zero network I/O.
    """
    state = _load_state(state_path)
    now = time.time()
    min_ev = config.MIN_EV_PERCENT if min_ev is None else min_ev

    client = DataGolfClient(last_request_ts=state.last_request_ts)
    did_fast = did_slow = False

    if now >= state.next_fast_due:
        print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] Fast tick: win/top_5/top_10/top_20...")
        records = _run_tasks(client, _fast_tasks())
        bets = [app_module.to_bet(r) for r in records]
        state.fast_bets = [b for b in bets if b["ev_percent"] >= min_ev]
        state.next_fast_due = now + FAST_INTERVAL_SECONDS
        did_fast = True
        print(f"  {len(state.fast_bets)} qualifying fast-group picks.")
        # Log + alert immediately -- don't make Telegram wait on the slower
        # grading cadence just because that's where it used to live.
        app_module.log_new_bets(state.fast_bets)

    if now >= state.next_slow_due:
        print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] Slow tick: mc/frl/matchups + grading...")
        records = _run_tasks(client, _slow_tasks())
        bets = [app_module.to_bet(r) for r in records]
        state.slow_bets = [b for b in bets if b["ev_percent"] >= min_ev]
        state.completed_by_tour = app_module._completed_events_by_tour(client)
        state.next_slow_due = now + SLOW_INTERVAL_SECONDS
        did_slow = True
        print(f"  {len(state.slow_bets)} qualifying slow-group picks.")

        app_module.log_new_bets(state.slow_bets)
        app_module.grade_and_write(client)

    state.last_request_ts = client.last_request_ts

    if not (did_fast or did_slow):
        _save_state(state, state_path)
        return {"did_fast": False, "did_slow": False}

    all_bets = sorted(state.fast_bets + state.slow_bets, key=lambda b: -b["ev_percent"])
    board_bets = app_module._filter_expired(all_bets, state.completed_by_tour)
    expired_count = len(all_bets) - len(board_bets)

    prev_keys_set = {tuple(k) for k in state.previous_keys} if state.previous_keys else None
    current_keys = {app_module._bet_key(b) for b in board_bets}
    new_keys = (current_keys - prev_keys_set) if prev_keys_set is not None else set()
    state.previous_keys = [list(k) for k in current_keys]

    _save_state(state, state_path)

    app_module.print_table(board_bets, new_keys)
    if expired_count:
        print(f"  {expired_count} pick(s) hidden from the board (tournament already finished).")

    dashboard.write(
        board_bets,
        {
            "last_scan": dashboard.now_str(),
            "api_rows": len(all_bets),
            "matched": len(all_bets),
            "min_ev": min_ev,
            "refresh_seconds": FAST_INTERVAL_SECONDS,
        },
        new_keys=new_keys,
    )
    print(f"  Board: {len(board_bets)} active picks. Dashboard updated: {dashboard.DASHBOARD_PATH}")

    discord_alert.send_bets(board_bets)

    return {"did_fast": did_fast, "did_slow": did_slow, "board_count": len(board_bets)}


def watch_fast(min_ev: float | None = None, poll_seconds: float = 3.0) -> None:
    """Persistent local loop -- calls tick() repeatedly; each call is a
    cheap no-op unless something is actually due."""
    print(
        f"Dual-cadence watch: fast group every {FAST_INTERVAL_SECONDS}s, "
        f"slow group (+ grading) every {SLOW_INTERVAL_SECONDS}s. Ctrl+C to stop."
    )
    while True:
        try:
            tick(min_ev=min_ev)
        except Exception as exc:  # keep the loop alive across transient errors
            print(f"Tick failed: {exc}")
        time.sleep(poll_seconds)
