"""DataGolf -> Bet365 +EV scanner.

Usage:
    python app.py                  one scan, print the best +EV bets
    python app.py --watch          rescan every WATCH_INTERVAL_SECONDS (.env)
    python app.py --diagnose       print per-request status/row-counts and
                                    sample JSON keys, useful when nothing
                                    is matching and you need to see what
                                    DataGolf actually returned
    python app.py --csv out.csv    also write results to a CSV file

Every scan also logs each qualifying pick as a paper bet in simulator.db,
grades any that have since settled, and rewrites simulator.html (a local
P&L dashboard, never published anywhere) -- see simulator_db.py,
grading.py, and simulator_dashboard.py.
"""

from __future__ import annotations

import argparse
import csv
import json
import time
from datetime import datetime

from tabulate import tabulate

import config
import dashboard
import discord_alert
import ev
import grading
import parsing
import simulator_dashboard
import simulator_db
import telegram_alert
from datagolf_client import DataGolfClient, RateLimitSuspended


# Tracks which bets appeared in the previous scan (within this running
# process) so new picks can be highlighted separately on the next refresh.
# None means "no prior scan yet" -- everything is new the very first time,
# which isn't a useful signal, so nothing gets flagged until scan #2.
_previous_keys: set[tuple] | None = None


def _bet_key(b: dict) -> tuple:
    return (b["market"], b["tour"], b["selection"])


def build_task_list() -> list[tuple[str, str, str]]:
    tasks = [("outrights", tour, market) for tour in config.TOURS for market in config.OUTRIGHT_MARKETS]
    if config.INCLUDE_MATCHUPS:
        for tour in config.TOURS:
            if tour not in config.MATCHUP_SUPPORTED_TOURS:
                continue
            for market in config.MATCHUP_MARKETS:
                tasks.append(("matchups", tour, market))
    return tasks


def _completed_events_by_tour(client: DataGolfClient) -> dict[str, list[str]]:
    """tour -> event names DataGolf's schedule marks completed.

    The odds feed keeps serving a finished tournament's stale lines for a
    while after the fact -- there's nothing left to actually bet on, so the
    board shouldn't keep showing them. The simulator is unaffected: it
    tracks every bet it ever logged regardless of what the board displays.
    """
    result: dict[str, list[str]] = {}
    for tour in config.TOURS:
        data = client.schedule(tour)
        names = []
        if isinstance(data, dict):
            for e in data.get("schedule", []):
                if e.get("status") == "completed":
                    names.append(e.get("event_name", ""))
        result[tour] = names
    return result


def _filter_expired(bets: list[dict], completed_by_tour: dict[str, list[str]]) -> list[dict]:
    active = []
    for b in bets:
        sched_names = completed_by_tour.get(b["tour"], [])
        if any(grading._event_names_match(b["event_name"], b["tour"], n) for n in sched_names):
            continue
        active.append(b)
    return active


def run_scan(client: DataGolfClient, keep_raw_samples: bool = False):
    tasks = build_task_list()
    records: list[parsing.BetRecord] = []
    raw_samples: dict[str, dict] = {}

    i = 0
    while i < len(tasks):
        kind, tour, market = tasks[i]
        try:
            data = client.outrights(tour, market) if kind == "outrights" else client.matchups(tour, market)
        except RateLimitSuspended as exc:
            wait_s = exc.args[0] if exc.args else 300.0
            print(
                f"  Rate limited by DataGolf, waiting {int(wait_s)}s before resuming "
                f"({len(records)} records collected so far, nothing lost)..."
            )
            time.sleep(wait_s)
            continue  # retry same task

        if data is not None:
            parsed = parsing.parse(data, tour, market)
            records.extend(parsed)
            if keep_raw_samples and kind not in raw_samples:
                raw_samples[kind] = data
        i += 1

    return records, raw_samples


def to_bet(record: parsing.BetRecord) -> dict:
    result = ev.evaluate(record.model_prob, record.book_decimal)
    selection = record.player_name
    if record.opponents:
        selection = f"{selection} vs {', '.join(record.opponents)}"
    return {
        "market": record.market.upper(),
        "tour": record.tour,
        "selection": selection,
        "player_name": record.player_name,
        "opponents": record.opponents,
        "round_num": record.round_num,
        "event_name": record.event_name,
        "book_decimal": record.book_decimal,
        "book_american": ev.to_american(record.book_decimal),
        "model_prob": record.model_prob,
        "fair_decimal": result.fair_decimal_odds,
        "ev_percent": result.ev_percent,
        "stake": result.kelly_stake,
    }


def _bet_row(b: dict) -> list:
    return [
        b["market"],
        b["tour"],
        b["selection"],
        b["book_american"],
        f"{b['model_prob'] * 100:.1f}%",
        f"{b['fair_decimal']:.2f}",
        f"{b['ev_percent']:+.1f}%",
        f"${b['stake']:.2f}",
    ]


_TABLE_HEADERS = ["MARKET", "TOUR", "SELECTION", "B365 (US)", "MODEL%", "FAIR", "EV%", "STAKE"]


def print_table(bets: list[dict], new_keys: set[tuple] = frozenset()) -> None:
    if not bets:
        print("\nNo +EV bets found at or above the configured threshold.")
        return

    new_bets = [b for b in bets if _bet_key(b) in new_keys]
    if new_bets:
        print(f"\n*** {len(new_bets)} NEW PICK(S) SINCE LAST SCAN ***")
        print(tabulate([_bet_row(b) for b in new_bets], headers=_TABLE_HEADERS, tablefmt="simple"))
        print()

    print(tabulate([_bet_row(b) for b in bets], headers=_TABLE_HEADERS, tablefmt="simple"))


def write_csv(bets: list[dict], path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "market", "tour", "selection", "event_name",
                "book_decimal", "book_american", "model_prob", "fair_decimal", "ev_percent", "stake",
            ],
        )
        writer.writeheader()
        writer.writerows(bets)


# The simulator is intentionally pickier than the live board -- it only
# "bets" the strongest signals, not every +EV pick shown on screen.
SIM_MIN_EV_PERCENT = 30.0
SIM_MATCHUP_MIN_EV_PERCENT = 5.0
SIM_MATCHUP_MARKETS = {"TOURNAMENT_MATCHUPS", "ROUND_MATCHUPS", "3_BALLS"}


def _passes_sim_threshold(b: dict) -> bool:
    if b["market"] in SIM_MATCHUP_MARKETS:
        return b["ev_percent"] >= SIM_MATCHUP_MIN_EV_PERCENT
    return b["ev_percent"] >= SIM_MIN_EV_PERCENT


def to_sim_bet(b: dict) -> simulator_db.SimBet:
    return simulator_db.SimBet(
        event_name=b["event_name"],
        tour=b["tour"],
        market=b["market"],
        player_name=b["player_name"],
        opponents=b["opponents"],
        round_num=b["round_num"],
        book_decimal=b["book_decimal"],
        book_american=b["book_american"],
        model_prob=b["model_prob"],
        fair_decimal=b["fair_decimal"],
        ev_percent=b["ev_percent"],
        stake=b["stake"],
    )


def run_simulator(client: DataGolfClient, bets: list[dict]) -> None:
    """Log every qualifying pick as a paper bet, grade whatever's now
    settled, and rewrite the local (never-published) simulator dashboard."""
    sim_bets = [to_sim_bet(b) for b in bets if b["event_name"] and _passes_sim_threshold(b)]
    newly_logged = simulator_db.log_bets(sim_bets)
    if newly_logged:
        print(f"  Simulator: logged {len(newly_logged)} new paper bet(s).")
        telegram_alert.send_new_bets(newly_logged)

    grade_summary = grading.grade_pending(client)
    if grade_summary["events_graded"]:
        print(
            f"  Simulator: graded {grade_summary['bets_graded']} bet(s) "
            f"across {grade_summary['events_graded']} newly-completed event(s)."
        )

    simulator_dashboard.write()


def print_diagnostics(client: DataGolfClient, raw_samples: dict) -> None:
    print("\n--- API Diagnostics ---")
    rows = [
        [d.endpoint, d.tour, d.market, d.status_code, d.row_count, d.note]
        for d in client.diagnostics
    ]
    print(tabulate(rows, headers=["endpoint", "tour", "market", "status", "rows", "note"], tablefmt="simple"))

    for kind, data in raw_samples.items():
        keys = parsing.sample_keys(data)
        print(f"\nSample keys for {kind}:")
        print(json.dumps(keys, indent=2))


def scan_once(args) -> None:
    client = DataGolfClient()
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Scanning DataGolf for Bet365 +EV...")

    records, raw_samples = run_scan(client, keep_raw_samples=args.diagnose)

    api_rows = sum(d.row_count for d in client.diagnostics)
    print(f"  {api_rows} API rows returned, {len(records)} Bet365/model rows matched.")

    bets = [to_bet(r) for r in records]
    min_ev = args.min_ev if args.min_ev is not None else config.MIN_EV_PERCENT
    bets = [b for b in bets if b["ev_percent"] >= min_ev]
    bets.sort(key=lambda b: b["ev_percent"], reverse=True)

    completed_by_tour = _completed_events_by_tour(client)
    board_bets = _filter_expired(bets, completed_by_tour)
    expired_count = len(bets) - len(board_bets)
    if expired_count:
        print(f"  {expired_count} pick(s) hidden from the board (tournament already finished).")

    global _previous_keys
    current_keys = {_bet_key(b) for b in board_bets}
    new_keys = (current_keys - _previous_keys) if _previous_keys is not None else set()
    _previous_keys = current_keys

    print_table(board_bets, new_keys)

    run_simulator(client, bets)  # unfiltered -- the simulator keeps everything

    if args.csv:
        write_csv(board_bets, args.csv)
        print(f"\nWrote {len(board_bets)} rows to {args.csv}")

    if args.diagnose:
        print_diagnostics(client, raw_samples)

    dashboard.write(
        board_bets,
        {
            "last_scan": dashboard.now_str(),
            "api_rows": api_rows,
            "matched": len(records),
            "min_ev": min_ev,
            "refresh_seconds": (args.interval or config.WATCH_INTERVAL_SECONDS) if args.watch else 60,
        },
        new_keys=new_keys,
    )
    print(f"\nDashboard updated: {dashboard.DASHBOARD_PATH}")

    discord_alert.send_bets(board_bets)


def main() -> None:
    parser = argparse.ArgumentParser(description="DataGolf -> Bet365 +EV scanner")
    parser.add_argument("--watch", action="store_true", help="rescan continuously")
    parser.add_argument("--interval", type=int, default=None, help="seconds between scans in --watch mode")
    parser.add_argument("--min-ev", type=float, default=None, help="minimum EV%% to display")
    parser.add_argument("--csv", type=str, default=None, help="write results to this CSV path")
    parser.add_argument("--diagnose", action="store_true", help="print per-request diagnostics")
    args = parser.parse_args()

    if not config.API_KEY:
        raise SystemExit(
            "DATAGOLF_API_KEY is not set. Copy .env.example to .env and paste your key in."
        )

    if not args.watch:
        scan_once(args)
        return

    interval = args.interval or config.WATCH_INTERVAL_SECONDS
    while True:
        try:
            scan_once(args)
        except Exception as exc:  # keep the watch loop alive across transient errors
            print(f"Scan failed: {exc}")
        print(f"\nNext scan in {interval}s... (Ctrl+C to stop)\n")
        time.sleep(interval)


if __name__ == "__main__":
    main()
