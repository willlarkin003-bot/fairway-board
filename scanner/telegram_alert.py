from __future__ import annotations

import time

import requests

import config
import simulator_db

MAX_BETS_PER_MESSAGE = 5


def send_new_bets(sim_bets: list[simulator_db.SimBet]) -> None:
    """Notify Telegram about paper bets the simulator just logged for the
    first time -- not a repost of the whole board, just what's genuinely new
    since the last scan.

    Sent alphabetically by player name, split into multiple messages of at
    most MAX_BETS_PER_MESSAGE bets each -- a single message listing dozens
    of picks is unreadable (and eventually hits Telegram's own length cap).
    """
    if not sim_bets:
        return
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        print("  Telegram: skipped, TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID not configured.")
        return

    ordered = sorted(sim_bets, key=lambda b: b.player_name.lower())
    batches = [
        ordered[i:i + MAX_BETS_PER_MESSAGE]
        for i in range(0, len(ordered), MAX_BETS_PER_MESSAGE)
    ]

    sent = 0
    for part, batch in enumerate(batches, start=1):
        text = _format_message(batch, part, len(batches), len(ordered))
        if _send_message(text):
            sent += 1
        if part < len(batches):
            time.sleep(1)  # stay well under Telegram's own per-chat rate limit

    print(f"  Telegram: sent {sent}/{len(batches)} message(s) for {len(ordered)} new bet(s).")


def _format_message(batch: list[simulator_db.SimBet], part: int, total_parts: int, total_bets: int) -> str:
    header = f"New simulator bet{'s' if total_bets != 1 else ''} ({total_bets})"
    if total_parts > 1:
        header += f" -- part {part}/{total_parts}"
    lines = [header, ""]
    for b in batch:
        selection = b.player_name
        if b.opponents:
            selection += f" vs {', '.join(b.opponents)}"
        lines.append(
            f"{selection} -- {b.market} ({b.tour})\n"
            f"{b.event_name}\n"
            f"B365: {b.book_american}  EV: {b.ev_percent:+.1f}%  Stake: ${b.stake:.2f}"
        )
    return "\n\n".join(lines)


def _send_message(text: str) -> bool:
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        resp = requests.post(
            url,
            json={"chat_id": config.TELEGRAM_CHAT_ID, "text": text[:4000]},
            timeout=10,
        )
        if resp.ok:
            return True
        print(f"  Telegram: send failed, HTTP {resp.status_code}: {resp.text[:200]}")
        return False
    except requests.RequestException as exc:
        print(f"  Telegram: send failed: {exc}")
        return False
