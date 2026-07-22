"""Projected-points scoring model per SRS Appendix A as amended by CR-2.

Score_i = Availability_i * SUM_k( w_k * component_k_i ) / SUM_k( w_k )
          over components k that are AVAILABLE for player i.

Components are registered in config.SOURCES; each is normalized to [0,1].
A component that is None for a player (source down, no key, not applicable
to their position) drops out and its weight is redistributed pro-rata across
the remaining terms — the generalization of the original FotMob-N/A rule
(FR-DATA-06). Price is NOT a score term (FR-SCORE-03).
"""
from . import fpl_api

# Display scale: map the [0,1] blend onto a familiar points-like range.
SCALE = 10.0


def _norm_rating(rating: float | None) -> float | None:
    """Match ratings live ~[5.5, 8.5] in practice; map to [0,1]."""
    if rating is None:
        return None
    return max(0.0, min(1.0, (rating - 5.5) / 3.0))


def _fixture_ease(fixtures: list[dict]) -> float:
    """FDR 2 (easiest) .. 5 (hardest) -> ease per fixture, summed over the GW
    so double gameweeks score higher and blanks score 0 (O-5)."""
    ease = 0.0
    for fx in fixtures:
        d = fx.get("difficulty") or 3
        ease += (5 - d) / 3.0          # FDR 2 -> 1.0, FDR 5 -> 0.0
        if fx.get("home"):
            ease += 0.1                 # mild home advantage
    return min(1.0, ease) if len(fixtures) <= 1 else min(1.5, ease)


def _norm_xp(ep_next: str | None, cap: float = 8.0) -> float:
    try:
        return max(0.0, min(1.0, float(ep_next) / cap))
    except (TypeError, ValueError):
        return 0.0


# Prior for a player the healthy rating source has never seen (~rating 5.74).
# Without this, an unrated academy player drops the rating term entirely and
# inherits his team's fixture ease at full redistributed weight — which is how
# never-played players once outscored Haaland. Unknown != unpenalized.
UNRATED_PRIOR = 0.08


def score_players(players: list[dict], fixtures_by_team: dict[int, list[dict]],
                  weights: dict[str, float], rating_source_ok: bool = True) -> None:
    """Mutates each player dict, adding 'score' and 'score_parts'.

    rating_source_ok: True when the rating source produced data this run.
    Then unrated players get UNRATED_PRIOR (conservative prior) instead of
    N/A-redistribution; redistribution remains for genuine source outages."""
    for p in players:
        avail = fpl_api.availability_multiplier(p)
        team_fixtures = fixtures_by_team.get(p["team"], [])

        rating = _norm_rating(p.get("fotmob_rating"))
        if rating is None and rating_source_ok:
            rating = UNRATED_PRIOR
        components: dict[str, float | None] = {
            "rating":  rating,
            "fixture": _fixture_ease(team_fixtures),
            "xp":      _norm_xp(p.get("ep_next")),
        }

        active = {k: v for k, v in components.items()
                  if v is not None and weights.get(k, 0) > 0}
        wsum = sum(weights[k] for k in active)
        blend = sum(weights[k] * v for k, v in active.items()) / wsum if wsum > 0 else 0.0

        # Blank gameweek: no fixture -> no points possible.
        if not team_fixtures:
            blend = 0.0

        p["score"] = round(avail * blend * SCALE, 3)
        p["score_parts"] = {
            "availability": round(avail, 2),
            "fixtures": len(team_fixtures),
            **{k: (None if v is None else round(v, 3)) for k, v in components.items()},
        }
