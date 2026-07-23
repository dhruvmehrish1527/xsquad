"""Rating-source orchestrator (CR-1): API-Football primary, FPL BPS fallback.

Fills p['fotmob_rating'] (field name kept for compatibility with scoring/UI —
it is 'the match rating' regardless of source) on a pseudo 0-10 scale and
records source health for the weights panel.
"""
from .. import db
from . import api_football, custom_rating, matching


def _season_from_bootstrap(bs: dict) -> int:
    """API-Football uses the season start year (2025 == 2025-26)."""
    try:
        return int(bs["events"][0]["deadline_time"][:4])
    except (KeyError, IndexError, ValueError):
        return 2025


PRIOR_RATING = 5.74      # neutral prior (matches scoring.UNRATED_PRIOR on 0-1 scale)
SHRINK_MINUTES = 900     # ~10 full matches of evidence for full confidence


def _bps_pseudo_rating(p: dict) -> float | None:
    """Map season BPS-per-90 onto the familiar ~5.5-8.5 rating scale,
    shrunk toward a neutral prior in proportion to sample size (spec §7).

    Without shrinkage, per-90 stats on tiny samples dominate: a keeper whose
    only 90 minutes were one clean-sheet game out-rated Haaland's 2,953
    minutes, filling the pre-season squad with fringe players.
    """
    minutes = p.get("minutes") or 0
    if minutes <= 0:
        return None
    bps90 = (p.get("bps") or 0) * 90 / minutes
    raw = 5.5 + min(3.0, max(0.0, bps90) / 12.0)
    shrunk = (minutes * raw + SHRINK_MINUTES * PRIOR_RATING) / (minutes + SHRINK_MINUTES)
    return round(shrunk, 2)


def attach_ratings(players: list[dict], teams: dict[int, dict], bs: dict,
                   force: bool = False) -> dict:
    """Returns {source, matched, state, label} and mutates players."""
    # Primary: our own engine (RATING_SPEC.md) — keyed by FPL element id,
    # so no name matching is needed. Populate via `python -m app.backtest`
    # or `python -m app.services.custom_rating --fetch`.
    season = _season_from_bootstrap(bs)
    season_label = f"{season}-{(season + 1) % 100:02d}"
    pub = custom_rating.published()
    # Season guard: FPL reassigns player IDs each season, so ratings published
    # for a previous season must never attach to the current bootstrap.
    if pub and pub.get("season") != season:
        pub = None
    # Why the engine isn't active — surfaced in the fallback label so the
    # status explains the PRIMARY source's state, not a mid-chain detail.
    if pub is None or not pub.get("ratings"):
        engine_why = f"custom engine awaiting {season_label} matches"
    elif not db.kv_get("custom_approved", False):
        engine_why = "custom engine inactive: backtest gate not passed"
    else:
        engine_why = ""
    if pub and pub.get("ratings") and db.kv_get("custom_approved", False):
        rmap = {int(k): v for k, v in pub["ratings"].items()}
        matched = 0
        for p in players:
            p["fotmob_rating"] = rmap.get(p["id"])
            matched += p["fotmob_rating"] is not None
        meta = {"source": "custom", "matched": matched, "state": "ok",
                "label": f"custom engine {pub.get('version', '')} ({matched} rated)"}
        db.kv_set("rating_status", {"state": meta["state"], "label": meta["label"]})
        return meta

    # Secondary: API-Football per-match ratings (dormant since CR-4 unless a
    # key was configured out-of-band; the UI no longer offers key entry).
    if api_football.enabled():
        rows = api_football.get_recent_ratings(season, force=force)
        if rows:
            matched = matching.attach_fotmob_ratings(players, rows, teams)
            coverage = db.kv_get("af_coverage", "")
            label = f"API-Football ok ({matched} matched" + (f", {coverage}" if coverage else "") + ")"
            meta = {"source": "api-football", "matched": matched, "state": "ok", "label": label}
            db.kv_set("rating_status", {"state": meta["state"], "label": meta["label"]})
            return meta

    # Fallback: FPL BPS pseudo-rating from bootstrap (no extra requests),
    # shrunk toward the prior by sample size.
    matched = 0
    for p in players:
        r = _bps_pseudo_rating(p)
        p["fotmob_rating"] = r
        p["fotmob_match_confidence"] = 100 if r is not None else 0
        matched += r is not None
    meta = {"source": "bps", "matched": matched, "state": "warn",
            "label": f"last-season BPS bridge — {engine_why} ({matched} rated)"}
    db.kv_set("rating_status", {"state": meta["state"], "label": meta["label"]})
    return meta


def status_info() -> tuple[str, str]:
    """('ok'|'warn'|'bad', label) for the weights panel."""
    saved = db.kv_get("rating_status")
    if saved:
        return saved["state"], saved["label"]
    if api_football.enabled():
        return "warn", "API-Football key set — fetches on next optimize"
    return "warn", "no API-Football key — FPL BPS fallback will be used"
