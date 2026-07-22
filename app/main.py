"""xSquad — FastAPI app (SW-03, UI-01..07)."""
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from . import db
from .config import DEFAULT_WEIGHTS, SOURCES, settings, LEGAL_FORMATIONS
from .services import fpl_api, ratings, scoring, optimizer, transfers

app = FastAPI(title="xSquad")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


# ---------------- data assembly ----------------

def load_weights() -> dict[str, float]:
    """Weights dict keyed by source; migrates the legacy 'fotmob' key (CR-1/2)."""
    saved = db.kv_get("weights") or {}
    if "fotmob" in saved:
        saved["rating"] = saved.pop("fotmob")
    return {**DEFAULT_WEIGHTS, **{k: float(v) for k, v in saved.items() if k in DEFAULT_WEIGHTS}}


def build_players(force: bool = False, target_gw: int | None = None):
    """Ingest + match + score the full player universe (FR-DATA-01..06)."""
    bs = fpl_api.bootstrap(force=force)
    fx = fpl_api.fixtures(force=force)
    current, nxt = fpl_api.current_gameweek(bs)
    gw = target_gw or nxt
    teams = fpl_api.team_map(bs)
    fixtures_by_team = fpl_api.gw_fixtures_by_team(fx, gw)

    players = [dict(e) for e in bs["elements"]]
    rating_meta = ratings.attach_ratings(players, teams, bs, force=force)
    scoring.score_players(players, fixtures_by_team, load_weights(),
                          rating_source_ok=rating_meta["matched"] > 0)

    for p in players:  # display fields (UI-02)
        p["team_name"] = teams[p["team"]]["short_name"]
        p["pos"] = fpl_api.POSITIONS[p["element_type"]]
        p["price"] = p["now_cost"] / 10
        opps = fixtures_by_team.get(p["team"], [])
        p["next_opp"] = ", ".join(
            f'{teams[o["opponent"]]["short_name"]}({"H" if o["home"] else "A"})' for o in opps
        ) or "—"
        p["flag"] = p.get("news") or ""
    meta = {
        "gameweek": gw, "current_gw": current,
        "rating_status": rating_meta["label"], "rating_matched": rating_meta["matched"],
        "n_players": len(players),
    }
    return players, meta


def parse_formation(value: str | None):
    if not value or value == "auto":
        return None
    d, m, f = (int(v) for v in value.split("-"))
    return (d, m, f)


# ---------------- pages ----------------

def _panel_ctx() -> dict:
    """Context for weights_panel.html: per-provider health + per-source rows."""
    health = {
        "rating": ratings.status_info(),
        "fpl": fpl_api.status_info(),
    }
    w = load_weights()
    sources = [{
        "key": s.key, "label": s.label, "value": w[s.key],
        "state": health[s.provider][0], "title": health[s.provider][1],
    } for s in SOURCES]
    return {
        "sources": sources,
        "legend": [("Ratings", *health["rating"]), ("FPL API", *health["fpl"])],
    }


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {
        "formations": [f"{d}-{m}-{f}" for d, m, f in sorted(LEGAL_FORMATIONS)],
        "team_id": db.kv_get("team_id", ""),
        "budget": settings.budget_tenths / 10,
        **_panel_ctx(),
    })


@app.post("/optimize", response_class=HTMLResponse)
def optimize(request: Request, formation: str = Form("auto"), force: bool = Form(False)):
    players, meta = build_players(force=force)
    fmt = parse_formation(formation)
    # Out-of-band refresh of the weights panel: sources were just probed, so
    # the red/green health indicators update without a page reload.
    panel_oob = templates.get_template("weights_panel.html").render(
        oob=True, **_panel_ctx())
    try:
        result = optimizer.optimize_squad(players, formation=fmt)
    except optimizer.Infeasible as e:
        err = templates.get_template("error.html").render(message=str(e))
        return HTMLResponse(err + panel_oob)

    club_counts = {}
    for p in result["squad"]:
        club_counts[p["team_name"]] = club_counts.get(p["team_name"], 0) + 1

    # Transfer + chip advice if a squad is imported (FR-TRANSFER, FR-CHIP)
    tplans, chips, state = None, None, None
    latest = db.squad_latest()
    if latest:
        _, state = latest
        tplans = transfers.suggest_transfers(
            players, state["element_ids"], state.get("free_transfers", 1),
            state.get("bank", 0), formation=fmt)
        if "error" not in tplans:
            chips = transfers.chip_advice(players, state["element_ids"],
                                          state.get("bank", 0), tplans, formation=fmt)

    # Real-units projection (kv set by `python -m app.backtest --calibrate`).
    proj_pts, proj_band = None, None
    cal = db.kv_get("points_calibration")
    if cal:
        exp = lambda s: cal["a"] + cal["b"] * s
        proj_pts = round(sum(exp(p["score"]) for p in result["xi"])
                         + exp(result["captain"]["score"]))   # captain counts double
        proj_band = round(cal["team_sd"])

    db.kv_set("last_result_gw", meta["gameweek"])
    squad_html = templates.get_template("squad.html").render(
        proj_pts=proj_pts, proj_band=proj_band,
        r=result, meta=meta, club_counts=club_counts,
        budget=settings.budget_tenths,
        remaining=settings.budget_tenths - result["cost"],
        over_cap=[c for c, n in club_counts.items() if n > settings.max_per_club],
        tplans=tplans, chips=chips, state=state,
        formation_mode="Auto" if fmt is None else f"{fmt[0]}-{fmt[1]}-{fmt[2]} (locked)",
    )
    return HTMLResponse(squad_html + panel_oob)


@app.post("/import", response_class=HTMLResponse)
def import_team(request: Request, team_id: int = Form(...)):
    try:
        bs = fpl_api.bootstrap()
        state = transfers.import_squad(team_id, bs)
        entry = fpl_api.entry(team_id)
        name = f'{entry.get("player_first_name","")} {entry.get("player_last_name","")}'.strip()

        # Render the imported 15 so the user can see exactly what was pulled.
        players, _ = build_players()
        by_id = {p["id"]: p for p in players}
        picks = [by_id[i] for i in state["element_ids"] if i in by_id]
        missing = len(state["element_ids"]) - len(picks)
        picks.sort(key=lambda p: (p["element_type"], -p["score"]))

        return templates.TemplateResponse(request, "import_result.html", {
            "team_name": entry.get("name", team_id), "manager": name,
            "state": state, "picks": picks, "missing": missing,
            "squad_value": sum(p["now_cost"] for p in picks) + state.get("bank", 0),
        })
    except Exception as e:  # FR-STATE-03: import failure is non-fatal
        return HTMLResponse(f'<div class="err">Import failed: {e}. '
                            f'You can still generate a from-scratch squad.</div>')


@app.post("/weights", response_class=HTMLResponse)
async def set_weights(request: Request):
    form = await request.form()
    current = load_weights()
    w = {}
    for s in SOURCES:
        try:
            w[s.key] = max(0.0, float(form.get(f"w_{s.key}", current[s.key])))
        except ValueError:
            w[s.key] = current[s.key]
    db.kv_set("weights", w)
    total = sum(w.values()) or 1.0
    normalized = " / ".join(f"{s.label} {w[s.key]/total:.2f}" for s in SOURCES)
    return HTMLResponse(f'<div class="ok">Weights saved (normalized: {normalized}). '
                        f'Re-run to apply.</div>')


@app.get("/export.json")
def export_json():
    """FR-UI-02: export last recommendation inputs."""
    players, meta = build_players()
    try:
        result = optimizer.optimize_squad(players)
    except optimizer.Infeasible as e:
        return JSONResponse({"error": str(e)}, status_code=422)
    slim = lambda p: {"id": p["id"], "name": p["web_name"], "club": p["team_name"],
                      "pos": p["pos"], "price": p["price"], "score": p["score"]}
    return {
        "gameweek": meta["gameweek"],
        "formation": "-".join(map(str, result["formation"])),
        "cost_m": result["cost"] / 10,
        "captain": slim(result["captain"]), "vice": slim(result["vice"]),
        "xi": [slim(p) for p in result["xi"]],
        "bench": [slim(p) for p in result["bench"]],
    }
