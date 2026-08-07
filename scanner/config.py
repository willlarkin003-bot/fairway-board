import os

from dotenv import load_dotenv

load_dotenv()


def _list(name: str, default: str) -> list[str]:
    raw = os.getenv(name, default)
    return [v.strip() for v in raw.split(",") if v.strip()]


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


API_KEY = os.getenv("DATAGOLF_API_KEY", "").strip()

TOURS = _list("TOURS", "pga,euro,kft,alt")
OUTRIGHT_MARKETS = _list("OUTRIGHT_MARKETS", "win,top_5,top_10,top_20,mc,frl")
MATCHUP_MARKETS = _list(
    "MATCHUP_MARKETS", "tournament_matchups,round_matchups,3_balls"
)
INCLUDE_MATCHUPS = _bool("INCLUDE_MATCHUPS", True)

# DataGolf only documents matchup odds for these tours.
MATCHUP_SUPPORTED_TOURS = {"pga", "euro", "opp", "alt"}

MIN_EV_PERCENT = _float("MIN_EV_PERCENT", 3.0)

BANKROLL = _float("BANKROLL", 1000.0)
KELLY_FRACTION = _float("KELLY_FRACTION", 0.25)
MAX_STAKE = _float("MAX_STAKE", 50.0)

REQUESTS_PER_MINUTE = _int("REQUESTS_PER_MINUTE", 40)
WATCH_INTERVAL_SECONDS = _int("WATCH_INTERVAL_SECONDS", 300)

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

BOOK = "bet365"
BASE_URL = "https://feeds.datagolf.com"
