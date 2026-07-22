"""Best-effort FotMob ratings source (SW-02, FR-DATA-03, C-5, DC-03).

FotMob has NO official API. This module probes known internal endpoint shapes
for the Premier League (league id 47) season player-rating leaderboard. Every
failure path returns None/{} so the app degrades to FPL-only scoring
(FR-DATA-06). Requests are throttled (COM-02) and cached for 6h.

NOTE (documented deviation): league-wide per-match logs for ~700 players would
require hundreds of requests per refresh, breaching PERF-02 and C-5 throttling.
v1 therefore uses the season-to-date average FotMob rating (refreshed weekly as
matches are played) as FotMobForm. get_recent_ratings() is the hook where a
true last-N recency-weighted form can be plugged in if a viable bulk source
appears.
"""
import time

import httpx

from ..config import settings
from .. import db

PL_LEAGUE_ID = 47
CACHE_KEY = "fotmob:ratings"
CACHE_AGE = 6 * 3600

_HEADERS_BASE = {
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
}

_last_request = 0.0


def _throttled_get(client: httpx.Client, url: str, **kw) -> httpx.Response:
    global _last_request
    wait = settings.fotmob_throttle_seconds - (time.time() - _last_request)
    if wait > 0:
        time.sleep(wait)
    _last_request = time.time()
    return client.get(url, timeout=settings.fotmob_timeout, **kw)


def _extract_rating_url(league_payload: dict) -> str | None:
    """Find the 'rating' stat list fetchAllUrl inside the league payload."""
    stats = (league_payload.get("stats") or {})
    for entry in stats.get("players", []):
        fetch_url = entry.get("fetchAllUrl") or ""
        if "rating" in fetch_url.lower() or "rating" in str(entry.get("header", "")).lower():
            if fetch_url:
                return fetch_url
    return None


def _parse_rating_list(payload: dict) -> list[dict]:
    """Normalize the rating leaderboard into [{name, team, rating}, ...]."""
    rows = (
        payload.get("TopLists", [{}])[0].get("StatList")
        if payload.get("TopLists")
        else payload.get("statList") or payload.get("topThree") or []
    ) or []
    out = []
    for r in rows:
        name = r.get("ParticipantName") or r.get("name")
        team = r.get("TeamName") or r.get("teamName") or ""
        rating = r.get("StatValue") or r.get("statValue")
        try:
            rating = float(rating)
        except (TypeError, ValueError):
            continue
        if name and 0 < rating <= 10:
            out.append({"name": name, "team": team, "rating": rating})
    return out


def get_recent_ratings(force: bool = False) -> list[dict] | None:
    """Return [{name, team, rating}, ...] or None if FotMob is unreachable.

    Never raises: any network/shape failure -> None (FR-DATA-06, REL-01).
    """
    if not settings.fotmob_enabled:
        return None
    if not force:
        cached = db.cache_get(CACHE_KEY, CACHE_AGE)
        if cached is not None:
            return cached or None  # empty list cached means "known unavailable"

    try:
        with httpx.Client(headers={**_HEADERS_BASE, "User-Agent": settings.user_agent},
                          follow_redirects=True) as client:
            # Probe 1: league payload -> discover the rating leaderboard URL.
            resp = _throttled_get(
                client, f"https://www.fotmob.com/api/leagues?id={PL_LEAGUE_ID}&tab=overview&type=league"
            )
            rating_url = None
            if resp.status_code == 200:
                rating_url = _extract_rating_url(resp.json())

            candidates = [u for u in [rating_url] if u]
            # Probe 2: known static-data shape (historically unauthenticated).
            candidates.append(
                f"https://data.fotmob.com/stats/{PL_LEAGUE_ID}/season/rating.json"
            )

            for url in candidates:
                try:
                    r2 = _throttled_get(client, url)
                    if r2.status_code != 200:
                        continue
                    rows = _parse_rating_list(r2.json())
                    if rows:
                        db.cache_put(CACHE_KEY, rows)
                        return rows
                except (httpx.HTTPError, ValueError):
                    continue
    except (httpx.HTTPError, ValueError, KeyError, IndexError):
        pass

    # Cache the failure briefly so we don't re-probe on every page load.
    db.cache_put(CACHE_KEY, [])
    return None


def status_info() -> tuple[str, str]:
    """('ok'|'warn'|'bad', label) for the UI source-health indicator."""
    if not settings.fotmob_enabled:
        return "bad", "disabled in settings"
    data = db.cache_get(CACHE_KEY)
    if data is None:
        return "warn", "not checked yet — run an optimization"
    if not data:
        return "bad", "unavailable — FPL-only fallback active"
    age = (db.cache_age(CACHE_KEY) or 0) / 3600
    return "ok", f"ok ({len(data)} players, {age:.1f}h old)"


def status() -> str:
    """Human-readable source status for the UI (FR-DATA-06)."""
    if not settings.fotmob_enabled:
        return "disabled"
    age = db.cache_age(CACHE_KEY)
    if age is None:
        return "not fetched"
    data = db.cache_get(CACHE_KEY)
    if not data:
        return "unavailable (FPL-only fallback active)"
    hours = age / 3600
    return f"ok ({len(data)} players, {hours:.1f}h old)"
