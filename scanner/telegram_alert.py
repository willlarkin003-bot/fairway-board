from __future__ import annotations

import requests

import config
import simulator_db


def send_new_bets(sim_bets: list[simulator_db.SimBet]) -> None:
    """Notify Telegram about paper bets the simulator just logged for the
    first time -- not a repost of the whole board, just what's genuinely new
    since the last scan."""
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID or not sim_bets:
        return

    plural = "s" if len(sim_bets) > 1 else ""
    lines = [f"New simulator bet{plural} logged ({len(sim_bets)}):", ""]
    for b in sim_bets[:15]:
        selection = b.player_name
        if b.opponents:
            selection += f" vs {', '.join(b.opponents)}"
        lines.append(
            f"{selection} -- {b.market} ({b.tour})\n"
            f"{b.event_name}\n"
            f"B365: {b.book_american}  EV: {b.ev_percent:+.1f}%  Stake: ${b.stake:.2f}"
        )
    if len(sim_bets) > 15:
        lines.append(f"...and {len(sim_bets) - 15} more.")
    text = "\n\n".join(lines)

    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        requests.post(
            url,
            json={"chat_id": config.TELEGRAM_CHAT_ID, "text": text[:4000]},
            timeout=10,
        )
    except requests.RequestException:
        pass
