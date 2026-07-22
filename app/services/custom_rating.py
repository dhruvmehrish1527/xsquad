"""Custom match-rating engine v1 — Tier 1 of RATING_SPEC.md.

Computes an independent per-player per-match rating (0-10, baseline 6.0) from
raw per-match stats in FPL's element-summary endpoint (xG, xA, xGC, saves,
defensive contributions, cards...). NOT FPL's points/BPS lens: our own
positional z-score formula per the spec.

Pipeline:
  fetch_all_histories()  -> cache 'fpl:summary:{id}' (per-GW rows, immutable)
  compute_all()          -> per-match ratings for every player
  publish()              -> cache 'custom:ratings' {player_id: last-5 form}
The app (ratings.py) reads the published map by FPL element id — no fuzzy
name matching needed. CLI:  python -m app.services.custom_rating --fetch
"""
import argparse
import math
import statistics
import time

import httpx

from ..config import settings
from .. import db

ENGINE_VERSION = "v1.1"  # v1.1: published form = rating x recent-minutes share
                         # (backtested +0.178 Spearman vs BPS +0.147; see backtest.py)
MIN_MINUTES = 15           # below this and no goal/assist -> no rating
BASELINE_MIN_MINUTES = 15  # rows entering positional baselines
RECENCY = [1.0, 0.85, 0.70, 0.55, 0.40]
TARGET_SD = 0.55           # calibration target (spec §5)
CLAMP_Z = 2.5

# Tier-1 stats entering positional baselines (per-90 μ/σ by element_type).
BASE_STATS = ["xg", "xa", "goals", "assists", "creativity", "threat",
              "saves", "xgc", "conceded", "defcon"]

# Position emphasis (element_type 1-4) per category (spec §3, collapsed to
# FPL's four positions for Tier 1).
EMPHASIS = {
    1: {"A": 0.0,  "B": 0.10, "C": 0.20, "D": 0.20, "E": 1.0, "F": 1.0},
    2: {"A": 0.55, "B": 0.60, "C": 0.80, "D": 1.00, "E": 0.9, "F": 0.0},
    3: {"A": 0.85, "B": 0.95, "C": 1.00, "D": 0.60, "E": 0.15, "F": 0.0},
    4: {"A": 1.00, "B": 0.80, "C": 0.70, "D": 0.30, "E": 0.0, "F": 0.0},
}
CAPS = {"A": 1.6, "B": 1.4, "C": 0.6, "D": 1.5, "E": 0.9, "F": 1.8}


# ---------------- ingestion ----------------

def fetch_all_histories(player_ids: list[int], force: bool = False,
                        sleep: float = 0.03, log_every: int = 100) -> int:
    """Fetch + cache element-summary history for each player. Returns #fetched."""
    fetched = 0
    with httpx.Client(headers={"User-Agent": settings.user_agent},
                      timeout=30.0, follow_redirects=True) as client:
        for i, pid in enumerate(player_ids):
            key = f"fpl:summary:{pid}"
            if not force and db.cache_get(key) is not None:
                continue
            r = client.get(f"https://fantasy.premierleague.com/api/element-summary/{pid}/")
            if r.status_code == 200:
                db.cache_put(key, r.json().get("history", []))
                fetched += 1
            time.sleep(sleep)
            if log_every and (i + 1) % log_every == 0:
                print(f"  …{i + 1}/{len(player_ids)} players")
    return fetched


def _row_stats(h: dict) -> dict:
    """Normalize one element-summary history row into engine stats."""
    f = lambda k: float(h.get(k) or 0)
    defcon = f("defensive_contribution") or (
        f("clearances_blocks_interceptions") + f("tackles") + f("recoveries"))
    return {
        "round": h.get("round"), "fixture": h.get("fixture"),
        "kickoff": h.get("kickoff_time") or "", "was_home": h.get("was_home"),
        "minutes": f("minutes"), "goals": f("goals_scored"), "assists": f("assists"),
        "xg": f("expected_goals"), "xa": f("expected_assists"),
        "xgc": f("expected_goals_conceded"), "conceded": f("goals_conceded"),
        "saves": f("saves"), "pens_saved": f("penalties_saved"),
        "pens_missed": f("penalties_missed"), "cs": f("clean_sheets"),
        "og": f("own_goals"), "yc": f("yellow_cards"), "rc": f("red_cards"),
        "creativity": f("creativity"), "threat": f("threat"),
        "defcon": defcon, "points": f("total_points"), "bps": f("bps"),
        "ict": f("ict_index"),
    }


# ---------------- engine ----------------

def _baselines(rows_by_pos: dict[int, list[dict]]) -> dict:
    """Per-position per-90 mean/sd for BASE_STATS."""
    out = {}
    for pos, rows in rows_by_pos.items():
        out[pos] = {}
        for s in BASE_STATS:
            per90 = [r[s] * 90 / r["minutes"] for r in rows
                     if r["minutes"] >= BASELINE_MIN_MINUTES]
            mu = statistics.mean(per90) if per90 else 0.0
            sd = statistics.pstdev(per90) if len(per90) > 1 else 1.0
            out[pos][s] = (mu, max(sd, 1e-6))
    return out


def _z(r: dict, stat: str, bl: dict) -> float:
    mu, sd = bl[stat]
    expected = mu * r["minutes"] / 90
    return max(-CLAMP_Z, min(CLAMP_Z, (r[stat] - expected) / sd))


def _surplus(r: dict, pos: int, bl: dict, fdr: float | None) -> float:
    """Raw pre-calibration surplus for one player-match (spec §2-4)."""
    z = lambda s: _z(r, s, bl)

    A = 0.55 * z("goals") + 0.30 * z("xg") + 0.15 * (z("goals") - z("xg")) \
        + (-0.45 * r["pens_missed"])
    B = 0.35 * z("assists") + 0.45 * z("xa") + 0.20 * z("creativity")
    C = 0.50 * z("creativity") + 0.50 * z("threat")   # Tier-1 proxies, small cap
    D = 0.60 * z("defcon") - 0.80 * r["og"]
    if pos in (1, 2):   # team defensive outcome: DEF + GK
        E = 0.45 * (1.0 if (r["cs"] and r["minutes"] >= 60) else 0.0) \
            - 0.35 * z("xgc") - 0.25 * z("conceded")
    else:
        E = 0.0
    if pos == 1:        # goalkeeping: xGC - GA as shot-stopping proxy (no PSxG in Tier 1)
        F = 0.40 * (z("xgc") - z("conceded")) + 0.35 * z("saves") \
            + 0.70 * r["pens_saved"]
    else:
        F = 0.0
    G = -0.15 * r["yc"] - 0.90 * r["rc"]

    cats = {"A": A, "B": B, "C": C, "D": D, "E": E, "F": F}
    total = sum(EMPHASIS[pos][c] * max(-CAPS[c], min(CAPS[c], v))
                for c, v in cats.items())
    total += G

    opp = 1.0 if fdr is None else max(0.85, min(1.15, 1 + 0.05 * (fdr - 3)))
    mins_factor = math.sqrt(min(1.0, r["minutes"] / 60))
    nudge = -0.05 if r["was_home"] else 0.05
    return total * opp * mins_factor + nudge


def _fixture_fdr_map(fixtures: list) -> dict[int, tuple]:
    return {fx["id"]: (fx.get("team_h_difficulty"), fx.get("team_a_difficulty"))
            for fx in fixtures}


def compute_all(elements: list[dict], fixtures: list) -> dict[int, list[dict]]:
    """player_id -> [{round, kickoff, rating, minutes, points, bps, ict}, ...]
    Ratings calibrated so surplus SD -> TARGET_SD (spec §5-6)."""
    pos_of = {e["id"]: e["element_type"] for e in elements}
    summaries = db.cache_get_many("fpl:summary:")
    fdr_map = _fixture_fdr_map(fixtures)

    parsed: dict[int, list[dict]] = {}
    rows_by_pos: dict[int, list[dict]] = {1: [], 2: [], 3: [], 4: []}
    for key, history in summaries.items():
        pid = int(key.rsplit(":", 1)[1])
        if pid not in pos_of:
            continue
        rows = [_row_stats(h) for h in (history or [])]
        rows = [r for r in rows if r["minutes"] > 0]
        parsed[pid] = rows
        rows_by_pos[pos_of[pid]].extend(rows)

    bl_by_pos = _baselines(rows_by_pos)

    # Pass 1: raw surpluses for eligible matches.
    raw: dict[int, list[tuple[dict, float]]] = {}
    all_surpluses = []
    for pid, rows in parsed.items():
        pos = pos_of[pid]
        out = []
        for r in rows:
            if r["minutes"] < MIN_MINUTES and (r["goals"] + r["assists"]) == 0:
                continue
            pair = fdr_map.get(r["fixture"])
            fdr = None
            if pair:
                fdr = pair[0] if r["was_home"] else pair[1]
            s = _surplus(r, pos, bl_by_pos[pos], fdr)
            out.append((r, s))
            all_surpluses.append(s)
        raw[pid] = out

    # Pass 2: calibrate to the target distribution.
    sd = statistics.pstdev(all_surpluses) if len(all_surpluses) > 1 else 1.0
    k = TARGET_SD / max(sd, 1e-6)
    result: dict[int, list[dict]] = {}
    for pid, pairs in raw.items():
        result[pid] = [{
            "round": r["round"], "kickoff": r["kickoff"],
            "rating": round(max(3.0, min(10.0, 6.0 + k * s)), 2),
            "minutes": r["minutes"], "points": r["points"],
            "bps": r["bps"], "ict": r["ict"],
        } for r, s in pairs]
        result[pid].sort(key=lambda m: m["kickoff"])
    return result


def form_rating(matches: list[dict], upto_round: int | None = None) -> float | None:
    """Last-5 recency-weighted form (newest first), optionally only rounds < upto."""
    pool = [m for m in matches if upto_round is None or m["round"] < upto_round]
    if not pool:
        return None
    last = sorted(pool, key=lambda m: m["kickoff"], reverse=True)[: len(RECENCY)]
    ws = RECENCY[: len(last)]
    return round(sum(m["rating"] * w for m, w in zip(last, ws)) / sum(ws), 2)


def effective_form(matches: list[dict], upto_round: int | None = None) -> float | None:
    """SHIPPED signal (v1.1): form rating x recent-minutes share.

    The pure rating measures quality per minute; next-GW points also depend
    heavily on playing volume. Backtest (GW6-38, 2025-26): pure +0.068,
    volume-adjusted +0.178, BPS baseline +0.147 -> this passes the ship gate."""
    f = form_rating(matches, upto_round)
    if f is None:
        return None
    pool = [m for m in matches if upto_round is None or m["round"] < upto_round]
    last = sorted(pool, key=lambda m: m["kickoff"], reverse=True)[: len(RECENCY)]
    ws = RECENCY[: len(last)]
    mins = sum(min(m["minutes"], 90) * w for m, w in zip(last, ws)) / sum(ws)
    return round(f * min(1.0, mins / 90), 2)


def publish(elements: list[dict], fixtures: list) -> dict:
    """Compute + store {player_id: form rating} for the app to consume."""
    per_player = compute_all(elements, fixtures)
    forms = {}
    for pid, matches in per_player.items():
        f = effective_form(matches)
        if f is not None:
            forms[pid] = f
    payload = {"version": ENGINE_VERSION, "ratings": forms,
               "n_players": len(forms),
               "n_matches": sum(len(m) for m in per_player.values())}
    db.cache_put("custom:ratings", payload)
    return payload


def published() -> dict | None:
    return db.cache_get("custom:ratings")


# ---------------- CLI ----------------

def main():
    ap = argparse.ArgumentParser(description="Custom rating engine v1")
    ap.add_argument("--fetch", action="store_true", help="fetch missing element summaries")
    ap.add_argument("--force", action="store_true", help="refetch all summaries")
    args = ap.parse_args()

    from . import fpl_api
    bs = fpl_api.bootstrap()
    fixtures = fpl_api.fixtures()
    elements = bs["elements"]

    if args.fetch or args.force:
        print(f"Fetching element summaries for {len(elements)} players…")
        n = fetch_all_histories([e["id"] for e in elements], force=args.force)
        print(f"Fetched {n} new summaries.")

    payload = publish(elements, fixtures)
    ratings = payload["ratings"]
    print(f"Engine {payload['version']}: rated {payload['n_players']} players "
          f"over {payload['n_matches']} player-matches.")
    if ratings:
        vals = sorted(ratings.values())
        mid = vals[len(vals) // 2]
        print(f"Form distribution: min {vals[0]} / median {mid} / max {vals[-1]}")
        name_of = {e["id"]: e["web_name"] for e in elements}
        top = sorted(ratings.items(), key=lambda kv: -kv[1])[:15]
        print("Top 15 by current form rating:")
        for pid, r in top:
            print(f"  {r:5.2f}  {name_of.get(pid, pid)}")


if __name__ == "__main__":
    main()
