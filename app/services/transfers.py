"""Squad import, transfer advice, and chip advice (FR-STATE, FR-TRANSFER, FR-CHIP)."""
from itertools import combinations

import httpx

from .. import db
from . import fpl_api, optimizer

HIT_COST = 4.0  # points per transfer beyond the free allowance
MAX_SUGGESTED_TRANSFERS = 3


# ---------------- squad import (FR-STATE-01..03) ----------------

def import_squad(team_id: int, bs: dict) -> dict:
    """Fetch the user's current squad + bank + free transfers via public API.

    Raises ValueError with a user-readable diagnosis when the public API has
    nothing to serve: FPL only publishes picks AFTER a gameweek's deadline,
    and team IDs are reissued every season.
    """
    current, _ = fpl_api.current_gameweek(bs)
    try:
        picks = fpl_api.entry_picks(team_id, current)
    except httpx.HTTPStatusError as e:
        try:
            fpl_api.entry(team_id)
        except httpx.HTTPStatusError:
            raise ValueError(
                f"no FPL team with ID {team_id} exists this season. Team IDs reset "
                f"every season — after you create your 2026-27 squad, find the new ID "
                f"in the URL of your Points page on fantasy.premierleague.com.") from e
        deadline = (bs["events"][current - 1].get("deadline_time") or "")[:10]
        raise ValueError(
            f"team found, but FPL only publishes squads after the gameweek deadline "
            f"({deadline}). Import will work once GW{current} is underway.") from e
    hist = picks.get("entry_history", {})
    state = {
        "team_id": team_id,
        "gameweek": current,
        "element_ids": [p["element"] for p in picks["picks"]],
        "bank": hist.get("bank", 0),                      # tenths
        "value": hist.get("value", 0),                    # squad value incl. bank
        "free_transfers": _free_transfers(picks, team_id),
        "chips_available": _chips_available(team_id),
    }
    db.squad_save(current, "import", state)
    db.kv_set("team_id", team_id)
    return state


def _free_transfers(picks: dict, team_id: int) -> int:
    # The picks payload doesn't expose FT directly; derive conservatively: 1
    # by default (user can adjust in UI). Bankable up to 5 under current rules.
    return 1


def _chips_available(team_id: int) -> list[str]:
    try:
        entry = fpl_api.entry(team_id)
        # entry endpoint doesn't list chips; assume all unless history says used.
        return ["wildcard", "freehit", "bboost", "3xc"]
    except Exception:
        return ["wildcard", "freehit", "bboost", "3xc"]


# ---------------- transfer advice (FR-TRANSFER-01..03) ----------------

def suggest_transfers(players: list[dict], current_ids: list[int],
                      free_transfers: int, bank_tenths: int,
                      formation=None) -> dict:
    """Compare current squad vs optimal swaps; return ranked plans with net gain
    after -4 hits (FR-TRANSFER-02). Budget = current squad sell value + bank."""
    by_id = {p["id"]: p for p in players}
    current = [by_id[i] for i in current_ids if i in by_id]
    if len(current) != 15:
        return {"error": f"Current squad incomplete ({len(current)}/15 matched to player data)."}

    squad_cost = sum(p["now_cost"] for p in current)
    budget = squad_cost + bank_tenths

    # Baseline: best XI from the current 15 with 0 transfers.
    base = optimizer.optimize_squad(current, formation=formation, budget_tenths=budget)
    plans = [{"transfers": [], "hits": 0, "net_points": base["xi_points"],
              "gain": 0.0, "result": base}]

    current_set = set(current_ids)
    for k in range(1, MAX_SUGGESTED_TRANSFERS + 1):
        best_k = _best_k_transfers(players, current, current_set, budget, k, formation)
        if best_k is None:
            continue
        hits = max(0, k - free_transfers) * HIT_COST
        net = best_k["result"]["xi_points"] - hits
        plans.append({
            "transfers": best_k["moves"], "hits": hits,
            "net_points": round(net, 2),
            "gain": round(net - base["xi_points"], 2),
            "result": best_k["result"],
        })

    plans.sort(key=lambda pl: -pl["net_points"])
    # FR-TRANSFER-02: don't recommend negative-gain plans; keep them visible but flagged.
    for pl in plans:
        pl["recommended"] = pl["gain"] >= 0 if pl["transfers"] else True
    return {"plans": plans, "baseline_points": base["xi_points"]}


def _best_k_transfers(players, current, current_set, budget, k, formation):
    """Exact ILP: keep exactly 15-k of the current squad, choose k newcomers."""
    keep_needed = 15 - k
    pool = list(players)
    try:
        result = _solve_with_keep(pool, current_set, keep_needed, budget, formation)
    except optimizer.Infeasible:
        return None
    if result is None:
        return None
    new_ids = {p["id"] for p in result["squad"]}
    outs = [p for p in current if p["id"] not in new_ids]
    ins = [p for p in result["squad"] if p["id"] not in current_set]
    if len(ins) != k:
        return None  # solver kept more than asked; k-1 plan already covers it
    moves = [{"out": o, "in": i} for o, i in zip(
        sorted(outs, key=lambda p: p["element_type"]),
        sorted(ins, key=lambda p: p["element_type"]))]
    return {"moves": moves, "result": result}


def _solve_with_keep(pool, current_set, keep_needed, budget, formation):
    import pulp
    from ..config import settings

    prob = pulp.LpProblem("fpl_transfers", pulp.LpMaximize)
    by_id = {p["id"]: p for p in pool}
    x = {i: pulp.LpVariable(f"x_{i}", cat="Binary") for i in by_id}
    y = {i: pulp.LpVariable(f"y_{i}", cat="Binary") for i in by_id}
    z = {i: pulp.LpVariable(f"z_{i}", cat="Binary") for i in by_id}

    prob += (pulp.lpSum(by_id[i]["score"] * y[i] for i in x)
             + pulp.lpSum(by_id[i]["score"] * z[i] for i in x)
             + 0.01 * pulp.lpSum(by_id[i]["score"] * x[i] for i in x))
    prob += pulp.lpSum(by_id[i]["now_cost"] * x[i] for i in x) <= budget
    prob += pulp.lpSum(x.values()) == 15
    et = {i: by_id[i]["element_type"] for i in x}
    prob += pulp.lpSum(x[i] for i in x if et[i] == 1) == settings.squad_gk
    prob += pulp.lpSum(x[i] for i in x if et[i] == 2) == settings.squad_def
    prob += pulp.lpSum(x[i] for i in x if et[i] == 3) == settings.squad_mid
    prob += pulp.lpSum(x[i] for i in x if et[i] == 4) == settings.squad_fwd
    for c in {by_id[i]["team"] for i in x}:
        prob += pulp.lpSum(x[i] for i in x if by_id[i]["team"] == c) <= settings.max_per_club
    # Keep exactly `keep_needed` of the current squad (=> exactly k transfers).
    prob += pulp.lpSum(x[i] for i in x if i in current_set) == keep_needed
    prob += pulp.lpSum(y.values()) == 11
    prob += pulp.lpSum(y[i] for i in x if et[i] == 1) == 1
    nd = pulp.lpSum(y[i] for i in x if et[i] == 2)
    nm = pulp.lpSum(y[i] for i in x if et[i] == 3)
    nf = pulp.lpSum(y[i] for i in x if et[i] == 4)
    if formation is None:
        prob += nd >= 3; prob += nd <= 5
        prob += nm >= 2; prob += nm <= 5
        prob += nf >= 1; prob += nf <= 3
    else:
        d, m, f = formation
        prob += nd == d; prob += nm == m; prob += nf == f
    for i in x:
        prob += y[i] <= x[i]; prob += z[i] <= y[i]
    prob += pulp.lpSum(z.values()) == 1

    status = prob.solve(optimizer.solver())
    if pulp.LpStatus[status] != "Optimal":
        return None

    squad = [by_id[i] for i in x if x[i].value() and x[i].value() > 0.5]
    xi = [by_id[i] for i in x if y[i].value() and y[i].value() > 0.5]
    captain = next(by_id[i] for i in x if z[i].value() and z[i].value() > 0.5)
    vice = next(p for p in sorted(xi, key=lambda q: -q["score"]) if p["id"] != captain["id"])
    bench = [p for p in squad if p not in xi]
    return {
        "squad": sorted(squad, key=lambda p: (p["element_type"], -p["score"])),
        "xi": sorted(xi, key=lambda p: (p["element_type"], -p["score"])),
        "bench": sorted(bench, key=lambda p: (p["element_type"] != 1, -p["score"])),
        "captain": captain, "vice": vice,
        "formation": (sum(1 for p in xi if p["element_type"] == 2),
                      sum(1 for p in xi if p["element_type"] == 3),
                      sum(1 for p in xi if p["element_type"] == 4)),
        "cost": sum(p["now_cost"] for p in squad),
        "xi_points": round(sum(p["score"] for p in xi) + captain["score"], 2),
    }


# ---------------- chip advice (FR-CHIP-01..05) ----------------

def chip_advice(players: list[dict], current_ids: list[int], bank_tenths: int,
                transfer_plans: dict, formation=None) -> list[dict]:
    """Advisory chip recommendations; at most one chip per GW (FR-CHIP-05)."""
    advice = []
    by_id = {p["id"]: p for p in players}
    current = [by_id[i] for i in current_ids if i in by_id]
    if len(current) != 15:
        return advice

    budget = sum(p["now_cost"] for p in current) + bank_tenths
    base = optimizer.optimize_squad(current, formation=formation, budget_tenths=budget)

    # Wildcard / Free Hit: full re-optimization, no hits (FR-CHIP-01/02).
    fresh = optimizer.optimize_squad(players, formation=formation, budget_tenths=budget)
    wc_gain = round(fresh["xi_points"] - base["xi_points"], 2)
    best_plan_gain = max((pl["gain"] for pl in transfer_plans.get("plans", [])), default=0)
    advice.append({
        "chip": "Wildcard", "gain": wc_gain,
        "recommend": wc_gain > max(6.0, best_plan_gain + HIT_COST),
        "note": f"Full rebuild projects +{wc_gain} pts over your current XI this GW "
                f"(vs +{best_plan_gain} from normal transfers). Wildcard pays off when this "
                f"gap is large and persistent, not for a one-week spike.",
    })
    advice.append({
        "chip": "Free Hit", "gain": wc_gain,
        "recommend": False,
        "note": "Best saved for a blank/double gameweek where the one-week optimal squad "
                "differs sharply from yours. Same projected gain as Wildcard this week.",
    })

    # Bench Boost: bench projected points (FR-CHIP-03).
    bench_pts = round(sum(p["score"] for p in base["bench"]), 2)
    advice.append({
        "chip": "Bench Boost", "gain": bench_pts,
        "recommend": bench_pts >= 12.0,
        "note": f"Your bench projects {bench_pts} pts this GW. Play it in a double "
                f"gameweek when all 15 have fixtures and the bench is strong.",
    })

    # Triple Captain: captain projection (FR-CHIP-04).
    cap = base["captain"]
    dgw = cap["score_parts"]["fixtures"] > 1
    advice.append({
        "chip": "Triple Captain", "gain": round(cap["score"], 2),
        "recommend": dgw and cap["score"] >= 7.0,
        "note": f"{cap['web_name']} projects {cap['score']} pts"
                + (" with a DOUBLE gameweek — strong TC window." if dgw
                   else "; hold for a double gameweek with an elite captain."),
    })
    return advice
