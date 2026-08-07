from __future__ import annotations

import requests

import config


def send_bets(bets: list[dict]) -> None:
    """POST the top +EV bets to a Discord webhook, if one is configured."""
    if not config.DISCORD_WEBHOOK_URL or not bets:
        return

    lines = ["**+EV Bet365 bets found**"]
    for b in bets[:10]:
        lines.append(
            f"**{b['selection']}** — {b['market']} ({b['tour']})\n"
            f"Bet365: {b['book_decimal']:.2f}  |  Fair: {b['fair_decimal']:.2f}  |  "
            f"EV: {b['ev_percent']:+.1f}%  |  Stake: ${b['stake']:.2f}"
        )
    content = "\n\n".join(lines)

    try:
        requests.post(config.DISCORD_WEBHOOK_URL, json={"content": content[:1900]}, timeout=10)
    except requests.RequestException:
        pass
