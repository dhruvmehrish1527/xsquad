"""Official FPL public API client (SW-01, FR-DATA-01/02, FR-STATE-01).

The reliable backbone: players, prices (tenths), form, availability, fixtures,
FDR, xP, and the user's squad via public team ID. All calls cached (FR-DATA-05).
"""
import httpx

from ..config import settings
from .. import db

BASE = "https://fantasy.premierleague.com/api"

POSITIONS = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}


def _get(path: str, cache_key: str, max_age: float | None = None, force: bool = False):
    if not force:
        cached = db.cache_get(cache_key, max_age or settings.freshness_seconds)
        if cached is not None:
            return cached
    try:
        resp = httpx.get(
            f"{BASE}/{path}",
            headers={"User-Agent": settings.user_agent},
            timeout=30.0,
            follow_redirects=True,
        )
        resp.raise_for_status()  # COM-02: surface 429s rather than hammering
        data = resp.json()
        db.cache_put(cache_key, data)
        db.kv_set("fpl_fetch_ok", True)
        return data
    except httpx.HTTPError:
        # REL-01: fall back to stale cache when the FPL API is unreachable.
        db.kv_set("fpl_fetch_ok", False)
        stale = db.cache_get(cache_key)  # any age
        if stale is not None:
            return stale
        raise


def status_info() -> tuple[str, str]:
    """('ok'|'warn'|'bad', label) for the UI source-health indicator."""
    age = db.cache_age("fpl:bootstrap")
    if age is None:
        return "warn", "not fetched yet"
    hours = age / 3600
    if db.kv_get("fpl_fetch_ok", True) is False:
        return "bad", f"unreachable — serving cache {hours:.1f}h old"
    return "ok", f"ok ({hours:.1f}h old)"


def bootstrap(force: bool = False) -> dict:
    """Players, teams, events (gameweeks), prices, form, availability."""
    return _get("bootstrap-static/", "fpl:bootstrap", force=force)


def fixtures(force: bool = False) -> list:
    return _get("fixtures/", "fpl:fixtures", force=force)


def entry(team_id: int) -> dict:
    return _get(f"entry/{team_id}/", f"fpl:entry:{team_id}", max_age=300)


def entry_picks(team_id: int, gw: int) -> dict:
    return _get(f"entry/{team_id}/event/{gw}/picks/", f"fpl:picks:{team_id}:{gw}", max_age=300)


def entry_transfers_state(team_id: int) -> dict | None:
    """Bank + free transfers come from the latest picks' entry_history."""
    try:
        return _get(f"entry/{team_id}/transfers/", f"fpl:transfers:{team_id}", max_age=300)
    except httpx.HTTPStatusError:
        return None


# ---------------- derived views ----------------

def current_gameweek(bs: dict) -> tuple[int, int]:
    """Returns (current_or_last_finished, next_gw) from bootstrap events.

    Pre-season, FPL marks GW1 as is_next and nothing as is_current — the
    target must be GW1, not GW2 (bug: Palmer showed his second fixture)."""
    current = next((ev["id"] for ev in bs["events"] if ev.get("is_current")), None)
    nxt = next((ev["id"] for ev in bs["events"] if ev.get("is_next")), None)
    if nxt is None:                      # season over: no next GW
        nxt = min(current + 1, 38) if current else 1
    if current is None:                  # pre-season: nothing played yet
        current = nxt
    return current, nxt


def season_of(bs: dict) -> int:
    """Season start year (2026 == 2026-27), from GW1's deadline. Player IDs
    are reassigned every season, so all ID-keyed data must be season-scoped."""
    try:
        return int(bs["events"][0]["deadline_time"][:4])
    except (KeyError, IndexError, TypeError, ValueError):
        return 0


def team_map(bs: dict) -> dict[int, dict]:
    return {t["id"]: t for t in bs["teams"]}


def gw_fixtures_by_team(fixtures_data: list, gw: int) -> dict[int, list[dict]]:
    """Team id -> list of that team's fixtures in the GW (handles doubles/blanks, O-5)."""
    out: dict[int, list[dict]] = {}
    for fx in fixtures_data:
        if fx.get("event") != gw:
            continue
        out.setdefault(fx["team_h"], []).append(
            {"opponent": fx["team_a"], "home": True, "difficulty": fx["team_h_difficulty"]}
        )
        out.setdefault(fx["team_a"], []).append(
            {"opponent": fx["team_h"], "home": False, "difficulty": fx["team_a_difficulty"]}
        )
    return out


def availability_multiplier(el: dict) -> float:
    """FR-SCORE-02: [0,1] from status + chance_of_playing."""
    status = el.get("status", "a")
    if status in ("i", "s", "u", "n"):   # injured, suspended, unavailable, not in squad
        return 0.0
    chance = el.get("chance_of_playing_next_round")
    if chance is None:
        return 1.0
    return max(0.0, min(1.0, chance / 100.0))
