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


def _bps_pseudo_rating(p: dict) -> float | None:
    """Map season BPS-per-90 onto the familiar ~5.5-8.5 rating scale.

    BPS is FPL's official per-match performance index (~40 actions). Season
    aggregate per-90 is the zero-extra-requests fallback signal.
    """
    minutes = p.get("minutes") or 0
    if minutes < 90:
        return None
    bps90 = (p.get("bps") or 0) * 90 / minutes
    return round(5.5 + min(3.0, max(0.0, bps90) / 12.0), 2)


def attach_ratings(players: list[dict], teams: dict[int, dict], bs: dict,
                   force: bool = False) -> dict:
    """Returns {source, matched, state, label} and mutates players."""
    # Primary: our own engine (RATING_SPEC.md) — keyed by FPL element id,
    # so no name matching is needed. Populate via `python -m app.backtest`
    # or `python -m app.services.custom_rating --fetch`.
    pub = custom_rating.published()
    # Season guard: FPL reassigns player IDs each season, so ratings published
    # for a previous season must never attach to the current bootstrap.
    if pub and pub.get("season") != _season_from_bootstrap(bs):
        pub = None
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

    # Secondary: API-Football per-match ratings, last-5 recency-weighted.
    if api_football.enabled():
        rows = api_football.get_recent_ratings(_season_from_bootstrap(bs), force=force)
        if rows:
            matched = matching.attach_fotmob_ratings(players, rows, teams)
            coverage = db.kv_get("af_coverage", "")
            label = f"API-Football ok ({matched} matched" + (f", {coverage}" if coverage else "") + ")"
            meta = {"source": "api-football", "matched": matched, "state": "ok", "label": label}
            db.kv_set("rating_status", {"state": meta["state"], "label": meta["label"]})
            return meta
        err = db.kv_get("af_last_error", "")
        fallback_reason = f"API-Football unavailable{' — ' + err if err else ''}"
    else:
        fallback_reason = "no API-Football key"

    # Fallback: FPL BPS pseudo-rating from bootstrap (no extra requests).
    matched = 0
    for p in players:
        r = _bps_pseudo_rating(p)
        p["fotmob_rating"] = r
        p["fotmob_match_confidence"] = 100 if r is not None else 0
        matched += r is not None
    meta = {"source": "bps", "matched": matched, "state": "warn",
            "label": f"FPL BPS fallback ({fallback_reason}; {matched} rated)"}
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
