"""One-off generator: builds the combined Board+Simulator snapshot artifact.

Not part of the app's runtime -- run manually whenever a fresh published
snapshot is wanted. Reads the current dashboard.html (live board) and
simulator.db (paper-trading ledger) and stitches them into a single
self-contained page with a client-side tab switch. Delete freely; app.py
does not import this.
"""
import html
import re
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import simulator_db
import simulator_dashboard as sd

EASTERN = ZoneInfo("America/New_York")

_args = sys.argv[1:]
LIVE = "--live" in _args
_args = [a for a in _args if a != "--live"]
OUT_PATH = _args[0] if _args else "artifact_snapshot.html"

# ---- Board tab: pull straight from the live dashboard.html the watch loop just wrote ----
with open("dashboard.html", encoding="utf-8") as f:
    dash = f.read()

meta_block = re.search(r'<div class="meta">(.*?)</div>', dash, re.S).group(1)
meta_text = re.sub(r"\s+", " ", meta_block).strip()
last_scan = re.search(r"Last scan:\s*([^&]+)&", meta_block).group(1).strip()
api_rows = re.search(r"([\d,]+)\s*API rows", meta_block).group(1)
matched = re.search(r"([\d,]+)\s*matched", meta_block).group(1)
min_ev = re.search(r"min EV\s*([\d.]+)%", meta_block).group(1)

row_matches = re.findall(r'<tr class="([^"]*)">\s*(.*?)\s*</tr>', dash, re.S)
board_total = len(row_matches)


def _board_row(classes: str, cells_html: str) -> str:
    cells = re.findall(r"<td[^>]*>(.*?)</td>", cells_html, re.S)
    market, tour, selection, price, model_pct, fair, ev_cell, stake = cells
    ev_match = re.match(r"([+\-][\d.]+%)\s*(<span[^>]*>verify</span>)?", ev_cell.strip())
    ev_val, verify = ev_match.group(1), ev_match.group(2)
    watch_class = "watch" if "caution" in classes else ""
    chip = ' <span class="chip">verify</span>' if verify else ""
    selection = re.sub(r'<span class="new-badge">NEW</span>', "", selection).strip()
    opp = ""
    if " vs " in selection:
        pick, opp_raw = selection.split(" vs ", 1)
        selection_html = f'<span class="pick">{pick}</span> <span class="opp">vs {opp_raw}</span>'
    else:
        selection_html = f'<span class="pick">{selection}</span>'
    return f"""
      <tr class="{watch_class}">
        <td class="market-tag">{market}</td>
        <td>{tour}</td>
        <td>{selection_html}</td>
        <td class="num odds">{price}</td>
        <td class="num">{model_pct}</td>
        <td class="num">{fair}</td>
        <td class="num ev">{ev_val}{chip}</td>
        <td class="num">{stake}</td>
      </tr>"""


board_rows_html = "".join(_board_row(c, cellhtml) for c, cellhtml in row_matches)

# ---- Simulator tab: reuse the same builders simulator_dashboard.py uses for simulator.html ----
now = datetime.now()
bounds = sd._period_bounds(now)
with simulator_db.connect(simulator_db.DB_PATH) as conn:
    cards_html = "".join(
        sd._stat_card_html(label, sd._summarize(conn, start)) for label, (start, _) in bounds.items()
    )
    all_rows = conn.execute("SELECT * FROM bets ORDER BY placed_at DESC").fetchall()
    total_bets = len(all_rows)
    first_placed = conn.execute("SELECT MIN(placed_at) AS d FROM bets").fetchone()["d"]

ledger_rows_html = "".join(sd._bet_row_html(r) for r in all_rows[:300]) or (
    '<tr><td colspan="8" class="empty">No simulated bets logged yet.</td></tr>'
)
daily = sd._group_by(all_rows, lambda dt: dt.strftime("%Y-%m-%d"))
weekly = sd._group_by(all_rows, lambda dt: (dt - __import__("datetime").timedelta(days=dt.weekday())).strftime("Week of %Y-%m-%d"))
monthly = sd._group_by(all_rows, lambda dt: dt.strftime("%Y-%m"))
daily_html = sd._rollup_table_html("Day by day", daily, limit=30)
weekly_html = sd._rollup_table_html("Week by week", weekly, limit=13)
monthly_html = sd._rollup_table_html("Month by month", monthly, limit=12)
tracking_note = f"Tracking since {first_placed[:10]}." if first_placed else "No bets logged yet."

generated_at = datetime.now(EASTERN).strftime("%Y-%m-%d %H:%M:%S %Z")
snapshot_note = (
    f"&#9873; refreshed continuously by a GitHub Action (about once a minute) &mdash; last updated {generated_at}"
    if LIVE else
    f"&#9873; one-time snapshot, generated {generated_at} &mdash; ask for a fresh one, this page does not auto-update"
)

TEMPLATE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Fairway Board</title>
<style>
  :root {{
    --bg: #faf6ec; --bg-panel: #f1e9d4; --bg-panel-alt: #ece2c7; --line: #ddd0ad;
    --ink: #221d12; --ink-dim: #6b5f42; --ink-faint: #948765; --accent: #8a6a26;
    --good: #15803d; --warn: #a35709; --warn-bg: rgba(163, 87, 9, 0.08);
    --lost: #b91c1c;
    --font-display: Bahnschrift, "SF Compact Condensed", "Segoe UI Semibold", system-ui, sans-serif;
    --font-body: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
    --font-mono: ui-monospace, "Cascadia Mono", "SFMono-Regular", Consolas, monospace;
    color-scheme: light dark;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --bg: #15130f; --bg-panel: #1c1912; --bg-panel-alt: #221e15; --line: #332c1f;
      --ink: #f3ead2; --ink-dim: #a89877; --ink-faint: #756a4e; --accent: #c9a24d;
      --good: #34d399; --warn: #f59e0b; --warn-bg: rgba(245, 158, 11, 0.10);
      --lost: #f87171;
    }}
  }}
  :root[data-theme="dark"] {{
    --bg: #15130f; --bg-panel: #1c1912; --bg-panel-alt: #221e15; --line: #332c1f;
    --ink: #f3ead2; --ink-dim: #a89877; --ink-faint: #756a4e; --accent: #c9a24d;
    --good: #34d399; --warn: #f59e0b; --warn-bg: rgba(245, 158, 11, 0.10);
    --lost: #f87171;
  }}
  :root[data-theme="light"] {{
    --bg: #faf6ec; --bg-panel: #f1e9d4; --bg-panel-alt: #ece2c7; --line: #ddd0ad;
    --ink: #221d12; --ink-dim: #6b5f42; --ink-faint: #948765; --accent: #8a6a26;
    --good: #15803d; --warn: #a35709; --warn-bg: rgba(163, 87, 9, 0.08);
    --lost: #b91c1c;
  }}

  * {{ box-sizing: border-box; }}
  body {{ background: var(--bg); color: var(--ink); font-family: var(--font-body);
          margin: 0; padding: 2.5rem 1.5rem 4rem; }}
  a {{ color: var(--accent); }}
  :focus-visible {{ outline: 2px solid var(--accent); outline-offset: 2px; }}

  .wrap {{ max-width: 68rem; margin: 0 auto; }}

  header.masthead {{ border-bottom: 2px solid var(--accent); padding-bottom: 0.9rem; margin-bottom: 0; }}
  .wordmark {{ font-family: var(--font-display); font-weight: 700; font-size: clamp(1.6rem, 4vw, 2.3rem);
               text-transform: uppercase; letter-spacing: 0.02em; margin: 0; text-wrap: balance; }}
  .wordmark span {{ color: var(--accent); }}
  .tagline {{ font-size: 0.85rem; color: var(--ink-dim); margin: 0.15rem 0 0; }}

  nav.tabs {{ display: flex; gap: 0.4rem; margin: 1.1rem 0 1.6rem; }}
  nav.tabs button {{ font-family: var(--font-display); font-size: 0.8rem; text-transform: uppercase;
                      letter-spacing: 0.04em; font-weight: 600; background: transparent;
                      color: var(--ink-dim); border: 1px solid var(--line); border-radius: 7px;
                      padding: 0.5rem 1.1rem; cursor: pointer; }}
  nav.tabs button.active {{ background: var(--accent); color: var(--bg); border-color: var(--accent); }}
  nav.tabs button:not(.active):hover {{ color: var(--ink); border-color: var(--ink-faint); }}

  .panel {{ display: none; }}
  .panel.active {{ display: block; }}

  .ticker {{ font-family: var(--font-mono); font-size: 0.78rem; color: var(--ink-faint);
             display: flex; flex-wrap: wrap; gap: 0.3rem 1.4rem; margin: 0 0 1.6rem;
             border-bottom: 1px solid var(--line); padding-bottom: 0.9rem; }}
  .ticker b {{ color: var(--ink-dim); font-weight: 600; }}

  .key {{ display: flex; flex-wrap: wrap; gap: 0.5rem 1.6rem; font-size: 0.82rem;
          color: var(--ink-dim); margin-bottom: 1.6rem; }}
  .key strong {{ color: var(--ink); }}

  .board-scroll {{ overflow-x: auto; border: 1px solid var(--line); border-radius: 10px; }}
  table {{ border-collapse: collapse; width: 100%; min-width: 54rem; }}
  .rollup table {{ min-width: 0; }}
  thead th {{ position: sticky; top: 0; background: var(--bg-panel); text-align: left;
              font-family: var(--font-display); font-weight: 600; text-transform: uppercase;
              letter-spacing: 0.06em; font-size: 0.72rem; color: var(--ink-dim);
              padding: 0.7rem 0.9rem; border-bottom: 1px solid var(--line); white-space: nowrap; }}
  th.num, td.num {{ text-align: right; }}
  tbody td {{ padding: 0.55rem 0.9rem; border-bottom: 1px solid var(--line); font-size: 0.87rem;
              vertical-align: middle; white-space: nowrap; }}
  tbody tr:nth-child(even) {{ background: var(--bg-panel-alt); }}
  tbody tr {{ border-left: 3px solid transparent; }}
  tbody tr.watch {{ border-left-color: var(--warn); background: var(--warn-bg); }}
  td.num, td.odds {{ font-family: var(--font-mono); font-variant-numeric: tabular-nums; }}
  td.ev {{ font-family: var(--font-mono); font-variant-numeric: tabular-nums; font-weight: 700; color: var(--good); }}
  tr.watch td.ev {{ color: var(--warn); }}
  .pick {{ color: var(--ink); font-weight: 600; }}
  .opp {{ color: var(--ink-faint); font-weight: 400; font-size: 0.72rem; }}
  .chip {{ display: inline-block; font-family: var(--font-body); font-size: 0.62rem; font-weight: 700;
           text-transform: uppercase; letter-spacing: 0.05em; background: var(--warn); color: #1a1200;
           border-radius: 3px; padding: 1px 5px; margin-left: 0.4rem; vertical-align: 1px; }}
  .market-tag {{ font-family: var(--font-mono); font-size: 0.72rem; color: var(--ink-dim); }}

  .stats {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(11rem, 1fr));
            gap: 0.8rem; margin-bottom: 2rem; }}
  .stat-card {{ background: var(--bg-panel); border: 1px solid var(--line); border-radius: 10px; padding: 0.9rem 1rem; }}
  .stat-label {{ font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.06em; color: var(--ink-dim); }}
  .stat-profit {{ font-family: var(--font-mono); font-size: 1.5rem; font-weight: 700; margin: 0.2rem 0 0.5rem; }}
  .stat-profit.pos {{ color: var(--good); }}
  .stat-profit.neg {{ color: var(--lost); }}
  .stat-profit.flat {{ color: var(--ink-dim); }}
  .stat-row {{ display: flex; justify-content: space-between; font-size: 0.78rem; color: var(--ink-dim);
               font-family: var(--font-mono); margin-top: 0.15rem; }}
  .stat-pending {{ font-size: 0.72rem; color: var(--warn); margin-top: 0.4rem; }}

  .status-won {{ color: var(--good); font-weight: 600; font-size: 0.75rem; }}
  .status-lost {{ color: var(--lost); font-weight: 600; font-size: 0.75rem; }}
  .status-push {{ color: var(--ink-dim); font-weight: 600; font-size: 0.75rem; }}
  .status-pending {{ color: var(--warn); font-weight: 600; font-size: 0.75rem; }}
  .payout-won {{ color: var(--good); font-weight: 700; }}
  .payout-lost {{ color: var(--lost); font-weight: 700; }}
  .payout-push, .payout-pending {{ color: var(--ink-faint); }}
  .empty {{ text-align: center; color: var(--ink-dim); padding: 2rem 0; }}

  .section-title {{ font-size: 0.95rem; margin: 2rem 0 0.6rem; color: var(--accent); }}
  .note {{ max-width: 46rem; font-size: 0.82rem; color: var(--ink-dim); line-height: 1.55; margin-top: 1.6rem; }}
  .note b {{ color: var(--ink); }}
  .snapshot-flag {{ display: inline-flex; align-items: center; gap: 0.4rem; font-family: var(--font-mono);
                     font-size: 0.75rem; color: var(--accent); border: 1px dashed var(--accent);
                     border-radius: 999px; padding: 0.3rem 0.8rem; margin-top: 1.8rem; }}
</style>
</head>
<body>
<div class="wrap">
  <header class="masthead">
    <h1 class="wordmark">Fairway <span>Board</span></h1>
    <p class="tagline">DataGolf model vs. Bet365 prices &mdash; positive-EV golf bets, plus the paper-trading simulator</p>
  </header>

  <nav class="tabs">
    <button type="button" class="active" data-tab="board">Board</button>
    <button type="button" data-tab="sim">Simulator</button>
  </nav>

  <section id="panel-board" class="panel active">
    <div class="ticker">
      <span><b>Scanned</b> {last_scan}</span>
      <span><b>{api_rows}</b> odds pulled</span>
      <span><b>{matched}</b> matched to a Bet365 price</span>
      <span><b>{board_total}</b> clear min. EV {min_ev}%</span>
      <span><b>Stakes</b> sized off a $2,000 bankroll, quarter-Kelly, $50 cap</span>
    </div>
    <div class="key">
      <span><strong>B365</strong> &mdash; Bet365's current American-odds price</span>
      <span><strong>Model%</strong> &mdash; DataGolf's win probability</span>
      <span><strong>Fair</strong> &mdash; the decimal odds that probability implies</span>
      <span><strong>EV%</strong> &mdash; edge if the model's right: (Model% &times; price) &minus; 1</span>
    </div>
    <div class="board-scroll">
      <table>
        <thead>
          <tr><th>Market</th><th>Tour</th><th>Pick</th><th class="num">B365</th><th class="num">Model%</th>
              <th class="num">Fair</th><th class="num">EV%</th><th class="num">Stake</th></tr>
        </thead>
        <tbody>{board_rows_html}
        </tbody>
      </table>
    </div>
    <p class="note">
      <b>Informational only, not betting advice.</b> Rows with a <span class="chip">verify</span> tag
      carry EV over 40% &mdash; almost always a longshot where a tiny error in DataGolf's probability
      estimate creates a huge apparent edge, or a book quietly leaving a thin line unattended. Check the
      price on DataGolf's own site and on Bet365 directly before betting those; treat the rest as a
      starting point, not gospel.
    </p>
  </section>

  <section id="panel-sim" class="panel">
    <p class="tagline" style="margin-bottom:1.4rem;">Only the strongest signals get paper-bet here: 30%+ EV on outright
      markets, 5%+ on matchups/3-balls &mdash; not every pick the board above shows. {total_bets} bets logged total.
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
        <tbody>{ledger_rows_html}
        </tbody>
      </table>
    </div>
    <p class="note">
      Stakes shown are exactly what the scanner recommended at pick time (flat, against the configured
      $2,000 bankroll &mdash; not compounding). Grading uses DataGolf's own results data and is a documented
      approximation of real bookmaker settlement rules, not exact for every edge case. "Pending" bets belong
      to tournaments that haven't finished yet.
    </p>
  </section>

  <div class="snapshot-flag">{snapshot_note}</div>
</div>
<script>
  document.querySelectorAll('nav.tabs button').forEach(function (btn) {{
    btn.addEventListener('click', function () {{
      document.querySelectorAll('nav.tabs button').forEach(function (b) {{ b.classList.remove('active'); }});
      document.querySelectorAll('.panel').forEach(function (p) {{ p.classList.remove('active'); }});
      btn.classList.add('active');
      document.getElementById('panel-' + btn.dataset.tab).classList.add('active');
    }});
  }});
</script>
</body>
</html>"""

content = TEMPLATE.format(
    last_scan=last_scan, api_rows=api_rows, matched=matched, board_total=board_total, min_ev=min_ev,
    board_rows_html=board_rows_html, total_bets=total_bets, tracking_note=tracking_note,
    cards_html=cards_html, daily_html=daily_html, weekly_html=weekly_html, monthly_html=monthly_html,
    ledger_rows_html=ledger_rows_html, snapshot_note=snapshot_note,
)

with open(OUT_PATH, "w", encoding="utf-8") as f:
    f.write(content)

print(f"Wrote {OUT_PATH} ({len(content)} bytes)")
