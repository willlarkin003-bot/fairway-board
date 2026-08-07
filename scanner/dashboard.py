"""Writes a self-contained, auto-refreshing HTML dashboard of the latest scan.

No server needed -- open the file directly in a browser. It reloads itself
every `refresh_seconds` via a <meta> tag, which just re-reads whatever
app.py wrote to disk on its last scan. It only updates if something (e.g.
`python app.py --watch`) is actually still running and rescanning.
"""

from __future__ import annotations

import html
from datetime import datetime
from zoneinfo import ZoneInfo

DASHBOARD_PATH = "dashboard.html"
EASTERN = ZoneInfo("America/New_York")

# Above this EV%, longshot/model-noise risk is high enough to flag visually
# rather than let it read as a normal, safe pick.
CAUTION_EV_THRESHOLD = 40.0


def _bet_key(b: dict) -> tuple:
    return (b["market"], b["tour"], b["selection"])


def _row_html(b: dict, is_new: bool = False) -> str:
    caution = b["ev_percent"] >= CAUTION_EV_THRESHOLD
    classes = " ".join(c for c in ("caution" if caution else "", "new-row" if is_new else "") if c)
    flag = ' <span class="flag">verify</span>' if caution else ""
    new_badge = ' <span class="new-badge">NEW</span>' if is_new else ""
    return f"""
    <tr class="{classes}">
      <td>{html.escape(b['market'])}</td>
      <td>{html.escape(b['tour'])}</td>
      <td>{html.escape(b['selection'])}{new_badge}</td>
      <td>{html.escape(b['book_american'])}</td>
      <td>{b['model_prob'] * 100:.1f}%</td>
      <td>{b['fair_decimal']:.2f}</td>
      <td class="ev">{b['ev_percent']:+.1f}%{flag}</td>
      <td>${b['stake']:.2f}</td>
    </tr>"""


_TABLE_HEAD = (
    "<tr><th>Market</th><th>Tour</th><th>Selection</th><th>B365 (US)</th>"
    "<th>Model%</th><th>Fair</th><th>EV%</th><th>Stake</th></tr>"
)


def write(bets: list[dict], meta: dict, path: str = DASHBOARD_PATH, new_keys: set[tuple] = frozenset()) -> None:
    refresh_seconds = meta.get("refresh_seconds", 60)
    new_bets = [b for b in bets if _bet_key(b) in new_keys]

    rows_html = "".join(_row_html(b, is_new=_bet_key(b) in new_keys) for b in bets) or (
        '<tr><td colspan="8" class="empty">No +EV bets at or above the current threshold.</td></tr>'
    )

    new_section_html = ""
    if new_bets:
        new_rows_html = "".join(_row_html(b, is_new=True) for b in new_bets)
        new_section_html = f"""
  <div class="new-section">
    <h2>New since last scan ({len(new_bets)})</h2>
    <table>
      <thead>{_TABLE_HEAD}</thead>
      <tbody>{new_rows_html}
      </tbody>
    </table>
  </div>"""

    content = f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta http-equiv="refresh" content="{refresh_seconds}">
<title>DataGolf -> Bet365 +EV Scanner</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font-family: -apple-system, Segoe UI, Roboto, sans-serif; margin: 2rem;
          background: #0f1115; color: #e6e6e6; }}
  h1 {{ font-size: 1.3rem; margin-bottom: 0.2rem; }}
  .meta {{ color: #9aa0a6; font-size: 0.9rem; margin-bottom: 1rem; }}
  table {{ border-collapse: collapse; width: 100%; }}
  th, td {{ text-align: left; padding: 6px 10px; border-bottom: 1px solid #2a2d34; font-size: 0.9rem; }}
  th {{ color: #9aa0a6; font-weight: 600; text-transform: uppercase; font-size: 0.75rem; }}
  td.ev {{ font-weight: 600; color: #4ade80; }}
  tr.caution td.ev {{ color: #fbbf24; }}
  .flag {{ font-size: 0.7rem; background: #7c2d12; color: #fed7aa; border-radius: 4px;
           padding: 1px 6px; margin-left: 6px; }}
  .empty {{ text-align: center; color: #9aa0a6; padding: 2rem 0; }}
  .disclaimer {{ margin-top: 1.5rem; font-size: 0.8rem; color: #6b7280; max-width: 60rem; }}
  .new-section {{ background: #132030; border: 1px solid #2563eb; border-radius: 8px;
                   padding: 0.25rem 1rem 1rem; margin-bottom: 1.5rem; }}
  .new-section h2 {{ font-size: 0.85rem; text-transform: uppercase; letter-spacing: 0.03em;
                      color: #60a5fa; margin: 1rem 0 0.5rem; }}
  .new-badge {{ font-size: 0.65rem; background: #1d4ed8; color: #dbeafe; border-radius: 4px;
                padding: 1px 6px; margin-left: 6px; }}
  @media (prefers-color-scheme: light) {{
    body {{ background: #fff; color: #111; }}
    .meta {{ color: #555; }}
    th {{ color: #555; }}
    th, td {{ border-bottom: 1px solid #e5e7eb; }}
    td.ev {{ color: #16a34a; }}
    tr.caution td.ev {{ color: #b45309; }}
    .disclaimer {{ color: #6b7280; }}
    .new-section {{ background: #eff6ff; border-color: #93c5fd; }}
    .new-section h2 {{ color: #1d4ed8; }}
  }}
</style>
</head>
<body>
  <h1>DataGolf &rarr; Bet365 +EV Scanner</h1>
  <div class="meta">
    Last scan: {html.escape(meta.get('last_scan', ''))} &middot;
    {meta.get('api_rows', 0)} API rows &middot; {meta.get('matched', 0)} matched &middot;
    min EV {meta.get('min_ev', 0):.1f}% &middot;
    auto-refreshes every {refresh_seconds}s
  </div>
{new_section_html}
  <table>
    <thead>
      {_TABLE_HEAD}
    </thead>
    <tbody>{rows_html}
    </tbody>
  </table>
  <div class="disclaimer">
    Informational only, not betting advice. Rows marked "verify" have EV over
    {CAUTION_EV_THRESHOLD:.0f}% -- usually a longshot where a small model error
    creates a large apparent edge. Check DataGolf's own site before betting those.
    This page only updates while a scanner (<code>python app.py --watch</code>) is
    still running.
  </div>
</body>
</html>"""

    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def now_str() -> str:
    return datetime.now(EASTERN).strftime("%Y-%m-%d %H:%M:%S %Z")
