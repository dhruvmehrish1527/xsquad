"""Backtest the custom rating engine (RATING_SPEC.md §6).

For each GW k: compute every player's last-5 form signal using ONLY matches
before k, then rank-correlate (Spearman) against actual FPL points scored in
GW k. Compared signals:

  custom  — engine v1 form rating
  bps     — last-5 recency-weighted BPS (the current fallback's basis)
  points  — last-5 recency-weighted FPL points (≈ FPL's own 'form')
  ict     — last-5 recency-weighted ICT index

Ship gate: custom must beat bps on mean Spearman (spec §6.2).
Note: positional baseline μ/σ are computed full-season (parameter-level
leakage only; documented v1 compromise — production uses trailing windows).

Run:  .venv/bin/python -m app.backtest
"""
import statistics
from collections import defaultdict

from . import db
from .services import custom_rating, fpl_api

START_GW = 6          # need some history before form means anything
SIGNALS = ["custom", "custom_vol", "bps", "points", "ict"]


def _rank(vals: list[float]) -> list[float]:
    """Average ranks (ties shared)."""
    order = sorted(range(len(vals)), key=lambda i: vals[i])
    ranks = [0.0] * len(vals)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and vals[order[j + 1]] == vals[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1
        for t in range(i, j + 1):
            ranks[order[t]] = avg
        i = j + 1
    return ranks


def spearman(x: list[float], y: list[float]) -> float | None:
    if len(x) < 25:
        return None
    rx, ry = _rank(x), _rank(y)
    mx, my = statistics.mean(rx), statistics.mean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = sum((a - mx) ** 2 for a in rx) ** 0.5
    dy = sum((b - my) ** 2 for b in ry) ** 0.5
    return num / (dx * dy) if dx and dy else None


def _weighted_last5(matches: list[dict], field: str, upto_round: int) -> float | None:
    pool = [m for m in matches if m["round"] < upto_round]
    if not pool:
        return None
    last = sorted(pool, key=lambda m: m["kickoff"], reverse=True)[:5]
    ws = custom_rating.RECENCY[: len(last)]
    return sum(m[field] * w for m, w in zip(last, ws)) / sum(ws)


def run() -> dict:
    bs = fpl_api.bootstrap()
    fixtures = fpl_api.fixtures()
    per_player = custom_rating.compute_all(bs["elements"], fixtures)
    max_round = max((m["round"] for ms in per_player.values() for m in ms), default=0)
    print(f"Backtesting GW{START_GW}-{max_round} over {len(per_player)} players…")

    per_gw: dict[str, list[float]] = defaultdict(list)
    for gw in range(START_GW, max_round + 1):
        preds: dict[str, list[float]] = {s: [] for s in SIGNALS}
        actual: list[float] = []
        for pid, matches in per_player.items():
            played = [m for m in matches if m["round"] == gw and m["minutes"] > 0]
            if not played:
                continue
            cf = custom_rating.form_rating(matches, upto_round=gw)
            mins5 = _weighted_last5(matches, "minutes", gw)
            sig = {
                "custom": cf,
                # quality x expected volume: rating form scaled by recent minutes
                "custom_vol": None if (cf is None or mins5 is None)
                              else cf * min(1.0, mins5 / 90),
                "bps": _weighted_last5(matches, "bps", gw),
                "points": _weighted_last5(matches, "points", gw),
                "ict": _weighted_last5(matches, "ict", gw),
            }
            if any(v is None for v in sig.values()):
                continue
            actual.append(sum(m["points"] for m in played))
            for s in SIGNALS:
                preds[s].append(sig[s])

        for s in SIGNALS:
            r = spearman(preds[s], actual)
            if r is not None:
                per_gw[s].append(r)

    summary = {s: statistics.mean(per_gw[s]) for s in SIGNALS if per_gw[s]}
    n_gws = len(per_gw["custom_vol"])
    wins = sum(1 for a, b in zip(per_gw["custom_vol"], per_gw["bps"]) if a > b)

    print(f"\nMean Spearman rank correlation with next-GW points ({n_gws} GWs):")
    for s in sorted(summary, key=summary.get, reverse=True):
        marker = "  <- SHIPPED signal (engine v1.1)" if s == "custom_vol" \
            else ("  <- pure quality rating (diagnostic)" if s == "custom" else "")
        print(f"  {s:10} {summary[s]:+.4f}{marker}")
    print(f"\ncustom_vol beats bps in {wins}/{n_gws} gameweeks")
    passed = summary.get("custom_vol", -1) > summary.get("bps", 0)
    verdict = "PASS — ship it" if passed else "FAIL — keep BPS fallback"
    print(f"Ship gate (custom_vol > bps): {verdict}")
    db.kv_set("custom_approved", passed)   # ratings.py only uses the engine if True
    return {"summary": summary, "per_gw": dict(per_gw), "verdict": verdict}


def _team_ease_by_round(fixtures: list) -> dict[tuple[int, int], float]:
    """(team_id, round) -> fixture ease, same formula as scoring._fixture_ease."""
    per: dict[tuple[int, int], list[float]] = defaultdict(list)
    for fx in fixtures:
        rnd = fx.get("event")
        if rnd is None:
            continue
        per[(fx["team_h"], rnd)].append((5 - (fx.get("team_h_difficulty") or 3)) / 3.0 + 0.1)
        per[(fx["team_a"], rnd)].append((5 - (fx.get("team_a_difficulty") or 3)) / 3.0)
    out = {}
    for key, eases in per.items():
        total = sum(eases)
        out[key] = min(1.0, total) if len(eases) <= 1 else min(1.5, total)
    return out


def sweep():
    """Grid-search the rating/fixture blend (the two historically testable
    terms) for max mean Spearman vs next-GW points. xP is untestable
    retroactively and is excluded (see module docstring)."""
    bs = fpl_api.bootstrap()
    fixtures = fpl_api.fixtures()
    per_player = custom_rating.compute_all(bs["elements"], fixtures)
    team_of = {e["id"]: e["team"] for e in bs["elements"]}
    ease = _team_ease_by_round(fixtures)
    max_round = max((m["round"] for ms in per_player.values() for m in ms), default=0)

    grid = [round(w * 0.1, 1) for w in range(11)]   # rating weight; fixture = 1-w
    scores: dict[float, list[float]] = {w: [] for w in grid}
    for gw in range(START_GW, max_round + 1):
        rows = []   # (rating_norm, ease_norm, actual_points)
        for pid, matches in per_player.items():
            played = [m for m in matches if m["round"] == gw and m["minutes"] > 0]
            if not played:
                continue
            f = custom_rating.effective_form(matches, upto_round=gw)
            if f is None:
                continue
            rn = max(0.0, min(1.0, (f - 5.5) / 3.0))        # scoring._norm_rating
            en = ease.get((team_of[pid], gw), 0.0) / 1.5     # scale to [0,1]
            rows.append((rn, en, sum(m["points"] for m in played)))
        if len(rows) < 25:
            continue
        actual = [r[2] for r in rows]
        for w in grid:
            pred = [w * r[0] + (1 - w) * r[1] for r in rows]
            r_s = spearman(pred, actual)
            if r_s is not None:
                scores[w].append(r_s)

    print(f"Blend sweep, rating-weight w vs fixture-weight (1-w), "
          f"GW{START_GW}-{max_round}:")
    best = None
    for w in grid:
        if not scores[w]:
            continue
        mean = statistics.mean(scores[w])
        bar = "#" * int((mean + 0.02) * 200)
        tag = ""
        if best is None or mean > best[1]:
            best = (w, mean)
        print(f"  rating {w:.1f} / fixture {1 - w:.1f} : {mean:+.4f}  {bar}")
    print(f"\nBest testable blend: rating {best[0]:.1f} / fixture {1 - best[0]:.1f} "
          f"(Spearman {best[1]:+.4f})")
    return best


def calibrate():
    """Fit blend-score -> actual next-GW FPL points (least squares) so the UI
    can display projections in real point units. Uses the two historically
    testable terms (rating, fixture) at the currently saved weight proportions;
    xP's share is approximated by them (documented compromise). Stores
    {a, b, player_sd, team_sd} in kv 'points_calibration'."""
    from .main import load_weights
    bs = fpl_api.bootstrap()
    fixtures = fpl_api.fixtures()
    per_player = custom_rating.compute_all(bs["elements"], fixtures)
    team_of = {e["id"]: e["team"] for e in bs["elements"]}
    ease = _team_ease_by_round(fixtures)
    max_round = max((m["round"] for ms in per_player.values() for m in ms), default=0)

    w = load_weights()
    wr, wf = w.get("rating", 0.5), w.get("fixture", 0.3)
    tot = (wr + wf) or 1.0
    wr, wf = wr / tot, wf / tot

    xs, ys = [], []
    for gw in range(START_GW, max_round + 1):
        for pid, matches in per_player.items():
            played = [m for m in matches if m["round"] == gw and m["minutes"] > 0]
            if not played:
                continue
            f = custom_rating.effective_form(matches, upto_round=gw)
            if f is None:
                continue
            rn = max(0.0, min(1.0, (f - 5.5) / 3.0))
            en = ease.get((team_of[pid], gw), 0.0) / 1.5
            xs.append(10 * (wr * rn + wf * en))          # same 0-10 scale as app scores
            ys.append(sum(m["points"] for m in played))

    n = len(xs)
    mx, my = statistics.mean(xs), statistics.mean(ys)
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / \
        sum((x - mx) ** 2 for x in xs)
    a = my - b * mx
    resid_sd = (sum((y - (a + b * x)) ** 2 for x, y in zip(xs, ys)) / (n - 2)) ** 0.5
    team_sd = resid_sd * (12 ** 0.5)   # 11 starters + captain double ~ 12 draws

    cal = {"a": round(a, 4), "b": round(b, 4),
           "player_sd": round(resid_sd, 2), "team_sd": round(team_sd, 1),
           "n": n, "weights_used": {"rating": wr, "fixture": wf}}
    db.kv_set("points_calibration", cal)
    print(f"Calibration on {n} player-GWs: E[pts] = {a:.2f} + {b:.3f} x score")
    print(f"per-player residual sd {resid_sd:.2f} -> team band ±{team_sd:.0f} pts")
    return cal


if __name__ == "__main__":
    import sys
    if "--sweep" in sys.argv:
        sweep()
    elif "--calibrate" in sys.argv:
        calibrate()
    else:
        run()
