"""Paper-trading ledger for simulated bets, stored locally in SQLite.

Every +EV pick a scan surfaces gets logged exactly once per event (a pick
that keeps showing up across repeated --watch cycles for the *same*
tournament is the same bet, not a new one each time -- the UNIQUE
constraint below is what enforces that). Once DataGolf reports an event as
completed, grading.py fills in the outcome.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime

DB_PATH = "simulator.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS bets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    placed_at TEXT NOT NULL,
    event_name TEXT NOT NULL,
    tour TEXT NOT NULL,
    market TEXT NOT NULL,
    player_name TEXT NOT NULL,
    opponents TEXT NOT NULL DEFAULT '[]',
    round_num INTEGER,
    book_decimal REAL NOT NULL,
    book_american TEXT NOT NULL,
    model_prob REAL NOT NULL,
    fair_decimal REAL NOT NULL,
    ev_percent REAL NOT NULL,
    stake REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    payout REAL,
    graded_at TEXT,
    grade_note TEXT,
    UNIQUE(event_name, tour, market, player_name)
);
CREATE INDEX IF NOT EXISTS idx_bets_status ON bets(status);
CREATE INDEX IF NOT EXISTS idx_bets_placed_at ON bets(placed_at);

CREATE TABLE IF NOT EXISTS graded_events (
    event_name TEXT NOT NULL,
    tour TEXT NOT NULL,
    graded_at TEXT NOT NULL,
    PRIMARY KEY (event_name, tour)
);
"""


@contextmanager
def connect(path: str = DB_PATH):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        conn.executescript(SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


@dataclass
class SimBet:
    event_name: str
    tour: str
    market: str
    player_name: str
    opponents: list[str]
    round_num: int | None
    book_decimal: float
    book_american: str
    model_prob: float
    fair_decimal: float
    ev_percent: float
    stake: float


def log_bets(bets: list[SimBet], path: str = DB_PATH) -> list[SimBet]:
    """Insert bets, silently skipping ones already logged for that event.

    Returns the subset that were newly inserted (i.e. genuinely new bets,
    not re-seen picks from a repeat scan of the same tournament).
    """
    inserted: list[SimBet] = []
    now = datetime.now().isoformat(timespec="seconds")
    with connect(path) as conn:
        for b in bets:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO bets (
                    placed_at, event_name, tour, market, player_name, opponents,
                    round_num, book_decimal, book_american, model_prob,
                    fair_decimal, ev_percent, stake
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    now, b.event_name, b.tour, b.market, b.player_name,
                    json.dumps(b.opponents), b.round_num, b.book_decimal,
                    b.book_american, b.model_prob, b.fair_decimal,
                    b.ev_percent, b.stake,
                ),
            )
            if cur.rowcount:
                inserted.append(b)
    return inserted


def pending_events(path: str = DB_PATH) -> list[tuple[str, str]]:
    """Distinct (event_name, tour) pairs that still have ungraded bets."""
    with connect(path) as conn:
        rows = conn.execute(
            "SELECT DISTINCT event_name, tour FROM bets WHERE status = 'pending'"
        ).fetchall()
    return [(r["event_name"], r["tour"]) for r in rows]


def pending_bets_for_event(event_name: str, tour: str, path: str = DB_PATH) -> list[sqlite3.Row]:
    with connect(path) as conn:
        return conn.execute(
            "SELECT * FROM bets WHERE event_name = ? AND tour = ? AND status = 'pending'",
            (event_name, tour),
        ).fetchall()


def apply_grade(bet_id: int, status: str, payout: float, note: str, path: str = DB_PATH) -> None:
    with connect(path) as conn:
        conn.execute(
            "UPDATE bets SET status = ?, payout = ?, graded_at = ?, grade_note = ? WHERE id = ?",
            (status, payout, datetime.now().isoformat(timespec="seconds"), note, bet_id),
        )


def mark_event_graded(event_name: str, tour: str, path: str = DB_PATH) -> None:
    with connect(path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO graded_events (event_name, tour, graded_at) VALUES (?, ?, ?)",
            (event_name, tour, datetime.now().isoformat(timespec="seconds")),
        )


def all_bets(path: str = DB_PATH) -> list[sqlite3.Row]:
    with connect(path) as conn:
        return conn.execute("SELECT * FROM bets ORDER BY placed_at DESC").fetchall()
