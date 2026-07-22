"""API-Football (api-sports.io) client — licensed per-match player ratings.

Replaces FotMob as the primary rating source (CR-1). Free tier limits are
100 requests/day AND ~10 requests/minute, so:
- finished fixtures are cached FOREVER (ratings are immutable once FT);
- each refresh spends at most REQUEST_BUDGET calls, paced by THROTTLE_S;
- first backfill may take a couple of runs to cover the full last-5 window,
  after which steady state is ~10 new fixtures per gameweek.

Any failure (no key, quota, network) returns None so ratings.py falls back
to FPL BPS (FR-DATA-06 / REL-01).
"""
import time

import httpx

from ..config import settings
from .. import db

BASE = "https://v3.football.api-sports.io"
PL_LEAGUE = 39
WINDOW_ROUNDS = 5           # last-N-matches window (O-2)
REQUEST_BUDGET = 45         # max API calls per refresh run
THROTTLE_S = 6.5            # ~9/min, under the 10/min free-tier cap
RECENCY_WEIGHTS = [1.0, 0.85, 0.70, 0.55, 0.40]  # most recent first
CACHE_AGE_RATINGS = 6 * 3600

_last_request = 0.0


def key() -> str | None:
    return db.kv_get("apifootball_key")


def enabled() -> bool:
    return bool(key())


def _get(client: httpx.Client, path: str, params: dict) -> dict | None:
    """One paced request; returns payload or None on any error."""
    global _last_request
    wait = THROTTLE_S - (time.time() - _last_request)
    if wait > 0:
        time.sleep(wait)
    _last_request = time.time()
    try:
        resp = client.get(f"{BASE}/{path}", params=params,
                          headers={"x-apisports-key": key()}, timeout=20.0)
        if resp.status_code != 200:
            return None
        data = resp.json()
        # API-Football signals quota/key problems inside a 200 body.
        if data.get("errors"):
            db.kv_set("af_last_error", str(data["errors"]))
            return None
        return data
    except (httpx.HTTPError, ValueError):
        return None


def validate_key(candidate: str) -> tuple[bool, str]:
    """1-request key check against /status."""
    try:
        resp = httpx.get(f"{BASE}/status", headers={"x-apisports-key": candidate},
                         timeout=15.0)
        data = resp.json()
        if data.get("errors"):
            return False, str(data["errors"])
        acct = data.get("response", {})
        req = acct.get("requests", {})
        return True, (f"plan {acct.get('subscription', {}).get('plan', '?')}, "
                      f"{req.get('current', '?')}/{req.get('limit_day', '?')} requests today")
    except (httpx.HTTPError, ValueError) as e:
        return False, str(e)


def get_recent_ratings(season: int, force: bool = False) -> list[dict] | None:
    """[{name, team, rating}] — recency-weighted over each player's last
    matches within the most recent WINDOW_ROUNDS*10 fixtures. None if unusable."""
    if not enabled():
        return None
    if not force:
        cached = db.cache_get("af:ratings", CACHE_AGE_RATINGS)
        if cached:
            return cached

    spent = 0
    with httpx.Client() as client:
        # 1 request: all finished PL fixtures this season (id + date).
        fx = _get(client, "fixtures", {"league": PL_LEAGUE, "season": season, "status": "FT"})
        spent += 1
        if fx is None or not fx.get("response"):
            return None
        fixtures = sorted(fx["response"], key=lambda f: f["fixture"]["date"], reverse=True)
        recent = fixtures[: WINDOW_ROUNDS * 10]

        # Per-fixture ratings, immutable => cache forever. Spend budget on gaps.
        per_fixture: list[tuple[str, list]] = []   # (date, player rows)
        for f in recent:
            fid = f["fixture"]["id"]
            ck = f"af:fix:{fid}"
            rows = db.cache_get(ck)
            if rows is None and spent < REQUEST_BUDGET:
                data = _get(client, "fixtures/players", {"fixture": fid})
                spent += 1
                if data is None:
                    continue
                rows = []
                for side in data.get("response", []):
                    tname = side.get("team", {}).get("name", "")
                    for entry in side.get("players", []):
                        st = (entry.get("statistics") or [{}])[0].get("games", {})
                        rating, minutes = st.get("rating"), st.get("minutes") or 0
                        if rating is None or minutes <= 0:
                            continue
                        rows.append({"name": entry["player"]["name"], "team": tname,
                                     "rating": float(rating)})
                db.cache_put(ck, rows)
            if rows:
                per_fixture.append((f["fixture"]["date"], rows))

    if not per_fixture:
        return None

    # Recency-weighted average per player over their appearances (newest first).
    per_fixture.sort(key=lambda t: t[0], reverse=True)
    acc: dict[str, dict] = {}
    for date, rows in per_fixture:
        for r in rows:
            k = f'{r["name"]}|{r["team"]}'
            a = acc.setdefault(k, {"name": r["name"], "team": r["team"], "vals": []})
            if len(a["vals"]) < len(RECENCY_WEIGHTS):
                a["vals"].append(r["rating"])
    out = []
    for a in acc.values():
        ws = RECENCY_WEIGHTS[: len(a["vals"])]
        rating = sum(v * w for v, w in zip(a["vals"], ws)) / sum(ws)
        out.append({"name": a["name"], "team": a["team"], "rating": round(rating, 2)})

    db.cache_put("af:ratings", out)
    db.kv_set("af_coverage", f"{len(per_fixture)}/{len(recent)} fixtures")
    return out
