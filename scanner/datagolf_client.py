"""Thin, rate-limited HTTP client for DataGolf's betting-tools endpoints.

DataGolf allows 45 requests/minute and suspends a key for 5 minutes if that's
exceeded. This client paces requests to stay under the limit, backs off (and
waits out) any 429, and records a diagnostics entry for every call so you can
see exactly what happened on a scan (status code, row count, url) instead of
just "nothing showed up".
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import requests

import config


class RateLimitSuspended(Exception):
    """Raised when DataGolf tells us to stop and wait out a suspension."""


@dataclass
class DiagnosticEntry:
    endpoint: str
    tour: str
    market: str
    status_code: int | None
    row_count: int
    note: str
    url: str


@dataclass
class DataGolfClient:
    api_key: str = field(default_factory=lambda: config.API_KEY)
    requests_per_minute: int = field(default_factory=lambda: config.REQUESTS_PER_MINUTE)
    session: requests.Session = field(default_factory=requests.Session)
    diagnostics: list[DiagnosticEntry] = field(default_factory=list)
    _last_request_ts: float = field(default=0.0, init=False)

    def __post_init__(self) -> None:
        self._min_interval = 60.0 / max(1, self.requests_per_minute)

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request_ts
        wait = self._min_interval - elapsed
        if wait > 0:
            time.sleep(wait)
        self._last_request_ts = time.monotonic()

    def _get(self, path: str, params: dict, tour: str, market: str) -> dict | list | None:
        if not self.api_key:
            raise RuntimeError(
                "DATAGOLF_API_KEY is not set. Put your key in the .env file."
            )

        params = {**params, "key": self.api_key, "file_format": "json"}
        url = f"{config.BASE_URL}{path}"

        self._throttle()
        try:
            resp = self.session.get(url, params=params, timeout=20)
        except requests.RequestException as exc:
            self.diagnostics.append(
                DiagnosticEntry(path, tour, market, None, 0, f"request failed: {exc}", url)
            )
            return None

        if resp.status_code == 429:
            retry_after = resp.headers.get("Retry-After")
            wait_s = float(retry_after) if retry_after else 300.0
            self.diagnostics.append(
                DiagnosticEntry(
                    path, tour, market, 429, 0,
                    f"rate limited, suspension ~{int(wait_s)}s", url,
                )
            )
            raise RateLimitSuspended(wait_s)

        if resp.status_code != 200:
            self.diagnostics.append(
                DiagnosticEntry(
                    path, tour, market, resp.status_code, 0,
                    resp.text[:200], url,
                )
            )
            return None

        try:
            data = resp.json()
        except ValueError:
            self.diagnostics.append(
                DiagnosticEntry(path, tour, market, 200, 0, "non-JSON response", url)
            )
            return None

        row_count = _guess_row_count(data)
        note = "ok" if row_count else "200 OK but 0 rows (likely no event/market right now)"
        self.diagnostics.append(
            DiagnosticEntry(path, tour, market, 200, row_count, note, url)
        )
        return data

    def outrights(self, tour: str, market: str):
        return self._get(
            "/betting-tools/outrights",
            {"tour": tour, "market": market, "odds_format": "decimal"},
            tour,
            market,
        )

    def matchups(self, tour: str, market: str):
        return self._get(
            "/betting-tools/matchups",
            {"tour": tour, "market": market, "odds_format": "decimal"},
            tour,
            market,
        )

    def schedule(self, tour: str):
        return self._get("/get-schedule", {"tour": tour}, tour, "schedule")

    def historical_rounds(self, tour: str, event_id: str, year: int):
        return self._get(
            "/historical-raw-data/rounds",
            {"tour": tour, "event_id": event_id, "year": str(year)},
            tour,
            f"rounds:{event_id}",
        )


def _guess_row_count(data) -> int:
    if isinstance(data, list):
        return len(data)
    if isinstance(data, dict):
        for key in ("odds", "match_list", "pairings", "data"):
            val = data.get(key)
            if isinstance(val, list):
                return len(val)
        # fall back: any list-of-dicts value in the payload
        for val in data.values():
            if isinstance(val, list) and val and isinstance(val[0], dict):
                return len(val)
    return 0
