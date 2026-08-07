"""Local-only HTML page showing simulated betting performance.

Same visual design as dashboard.html (dark board, gold accent, tabular
numbers) but built around P&L instead of live picks: rollups for today,
this week, this month, this year, and all-time, plus the full bet ledger.

Not published anywhere -- written to simulator.html next to app.py, opened
the same way as dashboard.html.
"""

from __future__ import annotations

import html
import json
import sqlite3
from datetime import datetime, timedelta

import simulator_db

SIMULATOR_PATH = "simulator.html"


def _period_bounds(now: datetime) -> dict[str, tuple[str, str]]:
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=today_start.weekday())  # Monday
    month_start = today_start.replace(day=1)
    year_start = today_start.replace(month=1, day=1)
    far_future = "9999-12-31"
    return {
        "Today": (today_start.isoformat(), far_future),
        "This week": (week_start.isoformat(), far_future),
        "This month": (month_start.isoformat(), far_future),
        "This year": (year_start.isoformat(), far_future),
        "All-time": ("0000-01-01", far_future),
    }


def _summarize(conn: sqlite3.Connection, start: str) -> dict:
    row = conn.execute(
        """
        SELECT
          COUNT(*) AS placed,
          SUM(CASE WHEN status = 'pending' THEN stake ELSE 0 END) AS staked_pending,
          SUM(CASE WHEN status != 'pending' THEN stake ELSE 0 END) AS staked_settled,
          SUM(CASE WHEN status = 'won' THEN 1 ELSE 0 END) AS wins,
          SUM(CASE WHEN status = 'lost' THEN 1 ELSE 0 END) AS losses,
          SUM(CASE WHEN status = 'push' THEN 1 ELSE 0 END) AS pushes,
          SUM(CASE WHEN status = 'void' THEN 1 ELSE 0 END) AS voids,
          SUM(CASE WHEN status != 'pending' THEN COALESCE(payout, 0) ELSE 0 END) AS profit
        FROM bets WHERE placed_at >= ?
        """,
        (start,),
    ).fetchone()
    decided = (row["wins"] or 0) + (row["losses"] or 0)
    win_rate = (row["wins"] / decided * 100) if decided else None
    staked_settled = row["staked_settled"] or 0
    roi = (row["profit"] / staked_settled * 100) if staked_settled else None
    return {
        "placed": row["placed"] or 0,
        "staked_pending": row["staked_pending"] or 0,
        "wins": row["wins"] or 0,
        "losses": row["losses"] or 0,
        "pushes": (row["pushes"] or 0) + (row["voids"] or 0),
        "win_rate": win_rate,
        "staked_settled": staked_settled,
        "profit": row["profit"] or 0,
        "roi": roi,
    }


def _stat_card_html(label: str, s: dict) -> str:
    profit = s["profit"]
    profit_class = "pos" if profit > 0 else ("neg" if profit < 0 else "flat")
    win_rate = f"{s['win_rate']:.0f}%" if s["win_rate"] is not None else "&mdash;"
    roi = f"{s['roi']:+.1f}%" if s["roi"] is not None else "&mdash;"
    pending_note = f'<div class="stat-pending">${s["staked_pending"]:.0f} pending</div>' if s["staked_pending"] else ""
    return f"""
    <div class="stat-card">
      <div class="stat-label">{html.escape(label)}</div>
      <div class="stat-profit {profit_class}">{profit:+.2f}</div>
      <div class="stat-row"><span>{s['wins']}-{s['losses']}-{s['pushes']}</span><span>{win_rate} win rate</span></div>
      <div class="stat-row"><span>${s['staked_settled']:.0f} staked</span><span>{roi} ROI</span></div>
      {pending_note}
    </div>"""


def _empty_group() -> dict:
    return {"placed": 0, "wins": 0, "losses": 0, "pushes": 0, "staked_settled": 0.0, "staked_pending": 0.0, "profit": 0.0}


def _group_by(rows: list[sqlite3.Row], key_fn) -> dict[str, dict]:
    groups: dict[str, dict] = {}
    for r in rows:
        dt = datetime.fromisoformat(r["placed_at"])
        key = key_fn(dt)
        g = groups.setdefault(key, _empty_group())
        g["placed"] += 1
        if r["status"] == "pending":
            g["staked_pending"] += r["stake"]
        else:
            g["staked_settled"] += r["stake"]
            g["profit"] += r["payout"] or 0.0
            if r["status"] == "won":
                g["wins"] += 1
            elif r["status"] == "lost":
                g["losses"] += 1
            else:
                g["pushes"] += 1
    return groups


def _rollup_table_html(title: str, groups: dict[str, dict], limit: int) -> str:
    keys = sorted(groups.keys(), reverse=True)[:limit]
    if not keys:
        rows_html = '<tr><td colspan="6" class="empty">No data yet.</td></tr>'
    else:
        rows_html = ""
        for key in keys:
            g = groups[key]
            decided = g["wins"] + g["losses"]
            win_rate = f"{g['wins'] / decided * 100:.0f}%" if decided else "&mdash;"
            roi = f"{g['profit'] / g['staked_settled'] * 100:+.1f}%" if g["staked_settled"] else "&mdash;"
            profit_class = "won" if g["profit"] > 0 else ("lost" if g["profit"] < 0 else "push")
            pending = f' <span class="opp">(${g["staked_pending"]:.0f} pending)</span>' if g["staked_pending"] else ""
            rows_html += f"""
            <tr>
              <td>{html.escape(key)}</td>
              <td class="num">{g['placed']}</td>
              <td class="num">{g['wins']}-{g['losses']}-{g['pushes']}</td>
              <td class="num">{win_rate}</td>
              <td class="num">${g['staked_settled']:.0f}{pending}</td>
              <td class="num payout-{profit_class}">{g['profit']:+.2f} ({roi})</td>
            </tr>"""
    return f"""
    <h2 class="section-title">{html.escape(title)}</h2>
    <div class="board-scroll rollup">
      <table>
        <thead>
          <tr><th>Period</th><th class="num">Bets</th><th class="num">W-L-P</th>
              <th class="num">Win%</th><th class="num">Staked</th><th class="num">Profit (ROI)</th></tr>
        </thead>
        <tbody>{rows_html}
        </tbody>
      </table>
    </div>"""


_STATUS_LABEL = {
    "won": ("WON", "won"),
    "lost": ("LOST", "lost"),
    "push": ("PUSH", "push"),
    "void": ("VOID", "push"),
    "pending": ("PENDING", "pending"),
}


def _bet_row_html(b: sqlite3.Row) -> str:
    label, cls = _STATUS_LABEL.get(b["status"], (b["status"].upper(), "pending"))
    payout = b["payout"]
    payout_html = f"{payout:+.2f}" if payout is not None else "&mdash;"
    selection = html.escape(b["player_name"])
    opponents = b["opponents"]
    try:
        opp_list = json.loads(opponents) if opponents else []
    except ValueError:
        opp_list = []
    if opp_list:
        selection += f' <span class="opp">vs {html.escape(", ".join(opp_list))}</span>'
    return f"""
    <tr>
      <td>{html.escape(b['placed_at'][:10])}</td>
      <td class="market-tag">{html.escape(b['market'])}</td>
      <td>{html.escape(b['tour'])}</td>
      <td>{selection}</td>
      <td class="num odds">{html.escape(b['book_american'])}</td>
      <td class="num">${b['stake']:.2f}</td>
      <td class="status-{cls}">{label}</td>
      <td class="num payout-{cls}">{payout_html}</td>
    </tr>"""


def write(path: str = SIMULATOR_PATH, db_path: str = simulator_db.DB_PATH) -> None:
    now = datetime.now()
    bounds = _period_bounds(now)

    with simulator_db.connect(db_path) as conn:
        cards_html = "".join(
            _stat_card_html(label, _summarize(conn, start)) for label, (start, _) in bounds.items()
        )
        all_rows = conn.execute("SELECT * FROM bets ORDER BY placed_at DESC").fetchall()
        total_bets = len(all_rows)
        first_placed = conn.execute("SELECT MIN(placed_at) AS d FROM bets").fetchone()["d"]

    rows_html = "".join(_bet_row_html(r) for r in all_rows[:500]) or (
        '<tr><td colspan="8" class="empty">No simulated bets logged yet.</td></tr>'
    )

    daily = _group_by(all_rows, lambda dt: dt.strftime("%Y-%m-%d"))
    weekly = _group_by(all_rows, lambda dt: (dt - timedelta(days=dt.weekday())).strftime("Week of %Y-%m-%d"))
    monthly = _group_by(all_rows, lambda dt: dt.strftime("%Y-%m"))

    daily_html = _rollup_table_html("Day by day", daily, limit=30)
    weekly_html = _rollup_table_html("Week by week", weekly, limit=13)
    monthly_html = _rollup_table_html("Month by month", monthly, limit=12)

    tracking_note = (
        f"Tracking since {first_placed[:10]}." if first_placed
        else "No bets logged yet."
    )

    content = f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta http-equiv="refresh" content="90">
<title>Fairway Board Simulator</title>
<style>
  :root {{ color-scheme: dark light; }}
  body {{ font-family: -apple-system, Segoe UI, Roboto, sans-serif; margin: 0;
          padding: 2.5rem 1.5rem 4rem; background: #15130f; color: #f3ead2; }}
  .wrap {{ max-width: 72rem; margin: 0 auto; }}
  h1 {{ font-size: 1.6rem; margin: 0 0 0.2rem; letter-spacing: 0.01em; }}
  h1 span {{ color: #c9a24d; }}
  .tagline {{ color: #a89877; font-size: 0.85rem; margin: 0 0 1.6rem; }}

  .stats {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(11rem, 1fr));
            gap: 0.8rem; margin-bottom: 2rem; }}
  .stat-card {{ background: #1c1912; border: 1px solid #332c1f; border-radius: 10px; padding: 0.9rem 1rem; }}
  .stat-label {{ font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.06em; color: #a89877; }}
  .stat-profit {{ font-family: ui-monospace, Consolas, monospace; font-size: 1.5rem; font-weight: 700;
                  margin: 0.2rem 0 0.5rem; }}
  .stat-profit.pos {{ color: #34d399; }}
  .stat-profit.neg {{ color: #f87171; }}
  .stat-profit.flat {{ color: #a89877; }}
  .stat-row {{ display: flex; justify-content: space-between; font-size: 0.78rem; color: #a89877;
               font-family: ui-monospace, Consolas, monospace; margin-top: 0.15rem; }}
  .stat-pending {{ font-size: 0.72rem; color: #f59e0b; margin-top: 0.4rem; }}

  .board-scroll {{ overflow-x: auto; border: 1px solid #332c1f; border-radius: 10px; }}
  table {{ border-collapse: collapse; width: 100%; min-width: 50rem; }}
  th {{ position: sticky; top: 0; background: #1c1912; text-align: left; font-size: 0.7rem;
        text-transform: uppercase; letter-spacing: 0.06em; color: #a89877; padding: 0.6rem 0.8rem;
        border-bottom: 1px solid #332c1f; white-space: nowrap; }}
  th.num, td.num {{ text-align: right; }}
  td {{ padding: 0.5rem 0.8rem; border-bottom: 1px solid #332c1f; font-size: 0.85rem; white-space: nowrap; }}
  tr:nth-child(even) td {{ background: #1a1710; }}
  td.odds, td.num {{ font-family: ui-monospace, Consolas, monospace; font-variant-numeric: tabular-nums; }}
  .opp {{ color: #756a4e; }}
  .market-tag {{ font-family: ui-monospace, Consolas, monospace; font-size: 0.72rem; color: #a89877; }}
  .status-won {{ color: #34d399; font-weight: 600; font-size: 0.75rem; }}
  .status-lost {{ color: #f87171; font-weight: 600; font-size: 0.75rem; }}
  .status-push {{ color: #a89877; font-weight: 600; font-size: 0.75rem; }}
  .status-pending {{ color: #f59e0b; font-weight: 600; font-size: 0.75rem; }}
  .payout-won {{ color: #34d399; font-weight: 700; }}
  .payout-lost {{ color: #f87171; font-weight: 700; }}
  .payout-push, .payout-pending {{ color: #756a4e; }}
  .empty {{ text-align: center; color: #a89877; padding: 2rem 0; }}
  .note {{ max-width: 46rem; font-size: 0.8rem; color: #756a4e; line-height: 1.5; margin-top: 1.5rem; }}
  .section-title {{ font-size: 0.95rem; margin: 2rem 0 0.6rem; color: #c9a24d; }}
  .rollup table {{ min-width: 0; }}
  .rollup .opp {{ font-size: 0.72rem; }}
  @media (prefers-color-scheme: light) {{
    body {{ background: #faf6ec; color: #221d12; }}
    .tagline {{ color: #6b5f42; }}
    .stat-card {{ background: #f1e9d4; border-color: #ddd0ad; }}
    .stat-label, .stat-row {{ color: #6b5f42; }}
    .stat-profit.flat {{ color: #6b5f42; }}
    .stat-profit.pos {{ color: #15803d; }}
    .stat-profit.neg {{ color: #b91c1c; }}
    th {{ background: #f1e9d4; color: #6b5f42; border-color: #ddd0ad; }}
    td {{ border-color: #ddd0ad; }}
    tr:nth-child(even) td {{ background: #ece2c7; }}
    .opp, .market-tag {{ color: #6b5f42; }}
    .status-won, .payout-won {{ color: #15803d; }}
    .status-lost, .payout-lost {{ color: #b91c1c; }}
    .note {{ color: #6b5f42; }}
    .section-title {{ color: #8a6a26; }}
  }}
</style>
</head>
<body>
  <div class="wrap">
    <h1>Fairway Board <span>Simulator</span></h1>
    <p class="tagline">Only the strongest signals get paper-bet here: 30%+ EV on outright markets,
      5%+ on matchups/3-balls -- not every pick the live board shows. {total_bets} bets logged total.
      {tracking_note}</p>

    <div class="stats">{cards_html}
    </div>

    {daily_html}
    {weekly_html}
    {monthly_html}

    <h2 class="section-title">Full ledger</h2>
    <div class="board-scroll">
      <table>
        <thead>
          <tr><th>Placed</th><th>Market</th><th>Tour</th><th>Pick</th><th class="num">B365</th>
              <th class="num">Stake</th><th>Status</th><th class="num">P/L</th></tr>
        </thead>
        <tbody>{rows_html}
        </tbody>
      </table>
    </div>

    <p class="note">
      Stakes shown are exactly what the scanner recommended at pick time (flat, against the configured
      $2,000 bankroll -- not compounding). Grading uses DataGolf's own results data and is a documented
      approximation of real bookmaker settlement rules, not exact for every edge case (see grading.py).
      "Pending" bets belong to tournaments that haven't finished yet. The day/week/month breakdown above
      only covers real tracked history -- it is not a backtest. DataGolf's API doesn't archive their own
      model's historical predictions (only a book's historical price and the real outcome), so there's no
      honest way to reconstruct what this scanner would have flagged before tracking started.
    </p>
  </div>
</body>
</html>"""

    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
