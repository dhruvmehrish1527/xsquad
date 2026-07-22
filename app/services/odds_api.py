"""The Odds API (the-odds-api.com) — anytime-goalscorer odds as a scoring term (CR-2).

Bookmaker markets are strong predictors of attacking returns. We fetch the
'player_goal_scorer_anytime' market for upcoming PL fixtures, convert median
decimal odds to implied probability, and expose [{name, team, prob}] rows.

Free tier: 500 credits/month; the event odds call costs credits per
region+market, so we use one region and cache for 6h. Any failure -> None
(graceful degradation, FR-DATA-06).
"""
import statistics

import httpx

from .. import db

BASE = "https://api.the-odds-api.com/v4"
SPORT = "soccer_epl"
MARKET = "player_goal_scorer_anytime"
REGION = "uk"
CACHE_KEY = "odds:probs"
CACHE_AGE = 6 * 3600
MAX_EVENTS = 10  # one PL gameweek


def key() -> str | None:
    return db.kv_get("oddsapi_key")


def enabled() -> bool:
    return bool(key())


def validate_key(candidate: str) -> tuple[bool, str]:
    """1-credit-free check: /sports listing."""
    try:
        resp = httpx.get(f"{BASE}/sports", params={"apiKey": candidate}, timeout=15.0)
        if resp.status_code != 200:
            return False, f"HTTP {resp.status_code}: {resp.text[:120]}"
        remaining = resp.headers.get("x-requests-remaining", "?")
        return True, f"{remaining} credits remaining this month"
    except httpx.HTTPError as e:
        return False, str(e)


def get_scorer_probs(force: bool = False) -> list[dict] | None:
    """[{name, team, prob}] for upcoming PL fixtures, or None."""
    if not enabled():
        return None
    if not force:
        cached = db.cache_get(CACHE_KEY, CACHE_AGE)
        if cached is not None:
            return cached or None

    try:
        with httpx.Client(timeout=20.0) as client:
            ev = client.get(f"{BASE}/sports/{SPORT}/events", params={"apiKey": key()})
            if ev.status_code != 200:
                db.kv_set("odds_last_error", f"HTTP {ev.status_code}")
                db.cache_put(CACHE_KEY, [])
                return None
            events = ev.json()[:MAX_EVENTS]
            if not events:
                db.kv_set("odds_last_error", "no upcoming PL fixtures")
                db.cache_put(CACHE_KEY, [])
                return None

            per_player: dict[str, dict] = {}
            for e in events:
                r = client.get(
                    f"{BASE}/sports/{SPORT}/events/{e['id']}/odds",
                    params={"apiKey": key(), "regions": REGION,
                            "markets": MARKET, "oddsFormat": "decimal"},
                )
                if r.status_code != 200:
                    continue
                prices: dict[str, list[float]] = {}
                for bk in r.json().get("bookmakers", []):
                    for mkt in bk.get("markets", []):
                        if mkt.get("key") != MARKET:
                            continue
                        for out in mkt.get("outcomes", []):
                            # Player props: 'description' holds the player name.
                            pname = out.get("description") or out.get("name")
                            price = out.get("price")
                            if pname and price and price > 1.0:
                                prices.setdefault(pname, []).append(price)
                for pname, ps in prices.items():
                    prob = 1.0 / statistics.median(ps)
                    row = per_player.setdefault(pname, {"name": pname, "team": "", "prob": 0.0})
                    row["prob"] = max(row["prob"], round(prob, 4))

            rows = list(per_player.values())
            db.cache_put(CACHE_KEY, rows)
            if rows:
                db.kv_set("odds_last_error", None)
            return rows or None
    except (httpx.HTTPError, ValueError, KeyError):
        db.kv_set("odds_last_error", "request failed")
        db.cache_put(CACHE_KEY, [])
        return None


def status_info() -> tuple[str, str]:
    """('ok'|'warn'|'bad', label) for the weights panel."""
    if not enabled():
        return "warn", "no Odds API key — term inactive (weight redistributed)"
    data = db.cache_get(CACHE_KEY)
    err = db.kv_get("odds_last_error")
    if data:
        age = (db.cache_age(CACHE_KEY) or 0) / 3600
        return "ok", f"ok ({len(data)} players priced, {age:.1f}h old)"
    if data is None:
        return "warn", "key set — fetches on next optimize"
    return "bad", f"unavailable ({err or 'unknown error'})"
