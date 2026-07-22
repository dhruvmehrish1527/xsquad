"""Exact ILP squad optimizer per SRS Appendix B (FR-OPT-01..06).

Joint squad(x) + XI(y) + captain(z) solve with PuLP/CBC:
  maximize  sum(s_i * y_i) + sum(s_i * z_i)          (captain counted twice)
subject to budget (C-1), 2/5/5/3 squad (C-2), <=3 per club (C-3),
legal or locked formation (C-4, FR-OPT-05), y<=x, z<=y.

Locking a formation biases the whole 15-man squad because x and y are solved
jointly (FR-OPT-06). A small epsilon on x keeps bench spots on the best cheap
options without distorting the XI objective.
"""
import pulp

from ..config import settings, LEGAL_FORMATIONS

BENCH_EPS = 0.01  # tie-break weight so bench slots pick the best-scoring cheap players


def solver():
    """HiGHS (native arm64) preferred; CBC fallback where its binary runs."""
    try:
        return pulp.HiGHS(msg=False, timeLimit=settings.solver_time_limit)
    except Exception:
        return pulp.PULP_CBC_CMD(msg=0, timeLimit=settings.solver_time_limit)


class Infeasible(Exception):
    """Raised with a human-readable reason (FR-OPT-03)."""


def _formation_ok(formation: tuple[int, int, int] | None) -> None:
    if formation is not None and tuple(formation) not in LEGAL_FORMATIONS:
        raise Infeasible(f"{formation[0]}-{formation[1]}-{formation[2]} is not a legal FPL formation")


def optimize_squad(players: list[dict], formation: tuple[int, int, int] | None = None,
                   budget_tenths: int | None = None,
                   locked_in: set[int] | None = None,
                   locked_out: set[int] | None = None) -> dict:
    """Returns {squad, xi, bench, captain, vice, formation, cost, objective}.

    formation: None = Auto mode (solver picks best legal shape);
               (DEF, MID, FWD) = Locked mode (FR-OPT-05).
    locked_in/locked_out: element ids forced in/out (used by transfer advisor).
    """
    _formation_ok(formation)
    budget = budget_tenths if budget_tenths is not None else settings.budget_tenths
    pool = [p for p in players if p.get("score", 0) is not None]
    if locked_out:
        pool = [p for p in pool if p["id"] not in locked_out]

    prob = pulp.LpProblem("fpl_squad", pulp.LpMaximize)
    x = {p["id"]: pulp.LpVariable(f"x_{p['id']}", cat="Binary") for p in pool}
    y = {p["id"]: pulp.LpVariable(f"y_{p['id']}", cat="Binary") for p in pool}
    z = {p["id"]: pulp.LpVariable(f"z_{p['id']}", cat="Binary") for p in pool}
    by_id = {p["id"]: p for p in pool}

    # Objective: XI + captain double, epsilon on squad for bench quality.
    prob += (
        pulp.lpSum(by_id[i]["score"] * y[i] for i in x)
        + pulp.lpSum(by_id[i]["score"] * z[i] for i in x)
        + BENCH_EPS * pulp.lpSum(by_id[i]["score"] * x[i] for i in x)
    )

    # C-1 budget (integer tenths, DC-02)
    prob += pulp.lpSum(by_id[i]["now_cost"] * x[i] for i in x) <= budget, "budget"
    # squad size + C-2 structure
    prob += pulp.lpSum(x.values()) == 15, "squad15"
    pos_of = {i: by_id[i]["element_type"] for i in x}  # 1 GK 2 DEF 3 MID 4 FWD
    prob += pulp.lpSum(x[i] for i in x if pos_of[i] == 1) == settings.squad_gk
    prob += pulp.lpSum(x[i] for i in x if pos_of[i] == 2) == settings.squad_def
    prob += pulp.lpSum(x[i] for i in x if pos_of[i] == 3) == settings.squad_mid
    prob += pulp.lpSum(x[i] for i in x if pos_of[i] == 4) == settings.squad_fwd
    # C-3 club cap
    clubs = {by_id[i]["team"] for i in x}
    for c in clubs:
        prob += pulp.lpSum(x[i] for i in x if by_id[i]["team"] == c) <= settings.max_per_club
    # XI + formation (C-4 / FR-OPT-05)
    prob += pulp.lpSum(y.values()) == 11, "xi11"
    prob += pulp.lpSum(y[i] for i in x if pos_of[i] == 1) == 1
    n_def = pulp.lpSum(y[i] for i in x if pos_of[i] == 2)
    n_mid = pulp.lpSum(y[i] for i in x if pos_of[i] == 3)
    n_fwd = pulp.lpSum(y[i] for i in x if pos_of[i] == 4)
    if formation is None:  # Auto: legal ranges
        prob += n_def >= 3; prob += n_def <= 5
        prob += n_mid >= 2; prob += n_mid <= 5
        prob += n_fwd >= 1; prob += n_fwd <= 3
    else:                  # Locked: exact equalities (Appendix B)
        d, m, f = formation
        prob += n_def == d; prob += n_mid == m; prob += n_fwd == f
    # linking + captain
    for i in x:
        prob += y[i] <= x[i]
        prob += z[i] <= y[i]
    prob += pulp.lpSum(z.values()) == 1, "one_captain"

    if locked_in:
        for pid in locked_in:
            if pid in x:
                prob += x[pid] == 1

    status = prob.solve(solver())
    if pulp.LpStatus[status] != "Optimal":
        raise Infeasible(_diagnose(pool, budget))

    squad = [by_id[i] for i in x if x[i].value() and x[i].value() > 0.5]
    xi = [by_id[i] for i in x if y[i].value() and y[i].value() > 0.5]
    captain = next(by_id[i] for i in x if z[i].value() and z[i].value() > 0.5)
    xi_sorted = sorted(xi, key=lambda p: -p["score"])
    vice = next(p for p in xi_sorted if p["id"] != captain["id"])
    bench_pool = [p for p in squad if p not in xi]
    bench_gk = [p for p in bench_pool if p["element_type"] == 1]
    bench_out = sorted([p for p in bench_pool if p["element_type"] != 1],
                       key=lambda p: -p["score"])  # bench order by score (FR-OPT-02)
    n_def_v = sum(1 for p in xi if p["element_type"] == 2)
    n_mid_v = sum(1 for p in xi if p["element_type"] == 3)
    n_fwd_v = sum(1 for p in xi if p["element_type"] == 4)

    return {
        "squad": sorted(squad, key=lambda p: (p["element_type"], -p["score"])),
        "xi": sorted(xi, key=lambda p: (p["element_type"], -p["score"])),
        "bench": bench_gk + bench_out,
        "captain": captain,
        "vice": vice,
        "formation": (n_def_v, n_mid_v, n_fwd_v),
        "cost": sum(p["now_cost"] for p in squad),
        "objective": round(pulp.value(prob.objective), 2),
        "xi_points": round(sum(p["score"] for p in xi) + captain["score"], 2),
    }


def _diagnose(pool: list[dict], budget: int) -> str:
    """Explain infeasibility (FR-OPT-03)."""
    cheapest = 0
    for et, need in ((1, settings.squad_gk), (2, settings.squad_def),
                     (3, settings.squad_mid), (4, settings.squad_fwd)):
        prices = sorted(p["now_cost"] for p in pool if p["element_type"] == et)
        if len(prices) < need:
            return f"Not enough players available at position type {et} ({len(prices)} < {need})."
        cheapest += sum(prices[:need])
    if cheapest > budget:
        return (f"Budget £{budget/10:.1f}m is below the cheapest legal squad "
                f"(£{cheapest/10:.1f}m).")
    return "No feasible squad under the current constraints (club cap or lock conflicts)."
