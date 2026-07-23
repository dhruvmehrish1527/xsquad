"""Render a static snapshot of this gameweek's optimal squad for GitHub Pages.

Run by .github/workflows/publish.yml on a weekly schedule after the data
fetch, backtest gate, and calibration. Writes a fully self-contained
site/index.html (inline CSS, no JS) showing the pitch, bench, and projection.

Run locally:  .venv/bin/python -m app.publish_static
"""
import datetime
import html
from pathlib import Path

from . import db
from .main import build_players
from .services import optimizer

OUT = Path(__file__).resolve().parent.parent / "site" / "index.html"

CSS = """
* { box-sizing:border-box; margin:0; }
body { font-family:-apple-system,'Segoe UI',Roboto,sans-serif; background:#1a1d24;
       color:#e8eaf0; padding-bottom:40px; }
header { background:#37003c; padding:16px 24px; }
header h1 { font-size:22px; color:#00ff87; display:inline; }
header span { color:#cbb3ce; font-size:13px; margin-left:12px; }
main { max-width:960px; margin:0 auto; padding:20px; }
.chips { display:flex; gap:10px; flex-wrap:wrap; margin-bottom:14px; }
.chip { background:#242833; border:1px solid #3a4050; border-radius:8px;
        padding:7px 13px; font-size:13px; }
.chip b { color:#00ff87; }
.pitch { background:linear-gradient(180deg,#2d8a4e,#237a41); border-radius:12px;
         padding:20px 10px; border:2px solid #1e6b38; }
.banner { text-align:center; margin-bottom:8px; }
.banner span { background:rgba(26,29,36,.92); border:1px solid rgba(255,255,255,.2);
               border-radius:20px; padding:6px 18px; font-size:14px; }
.banner b { color:#00ff87; }
.row { display:flex; justify-content:center; gap:12px; margin:12px 0; flex-wrap:wrap; }
.card { background:rgba(26,29,36,.92); border-radius:8px; padding:8px 10px;
        min-width:120px; text-align:center; position:relative;
        border:1px solid rgba(255,255,255,.15); }
.card .nm { font-weight:700; font-size:13px; }
.card .mt { font-size:11px; color:#9aa1b3; margin-top:2px; }
.card .sc { color:#00ff87; font-weight:700; font-size:12px; margin-top:3px; }
.badge { position:absolute; top:-8px; right:-6px; background:#e90052; color:#fff;
         font-size:10px; font-weight:800; border-radius:50%; width:20px;
         height:20px; line-height:20px; }
.badge.v { background:#3d5afe; }
.bench { display:flex; gap:12px; justify-content:center; flex-wrap:wrap;
         background:#242833; border:1px dashed #3a4050; border-radius:10px;
         padding:12px; margin-top:12px; }
h2 { font-size:13px; color:#9aa1b3; text-transform:uppercase; margin:16px 0 4px; }
footer { text-align:center; color:#9aa1b3; font-size:12px; margin-top:24px; }
footer a { color:#00ff87; }
"""


def _card(p: dict, captain_id: int, vice_id: int) -> str:
    badge = ""
    if p["id"] == captain_id:
        badge = '<div class="badge">C</div>'
    elif p["id"] == vice_id:
        badge = '<div class="badge v">V</div>'
    rating = f'R {p["fotmob_rating"]:.2f}' if p.get("fotmob_rating") else "R n/a"
    return (f'<div class="card">{badge}<div class="nm">{html.escape(p["web_name"])}</div>'
            f'<div class="mt">{p["team_name"]} · {p["pos"]} · £{p["price"]:.1f}m</div>'
            f'<div class="mt">vs {html.escape(p["next_opp"])}</div>'
            f'<div class="sc">{p["score"]:.2f} · {rating}</div></div>')


def main() -> None:
    players, meta = build_players(force=True)
    r = optimizer.optimize_squad(players)

    cal = db.kv_get("points_calibration")
    banner = ""
    if cal:
        exp = lambda s: cal["a"] + cal["b"] * s
        pts = round(sum(exp(p["score"]) for p in r["xi"]) + exp(r["captain"]["score"]))
        banner = (f'<div class="banner"><span>Projected: <b>~{pts} FPL pts</b> '
                  f'±{round(cal["team_sd"])} (incl. captain ×2)</span></div>')

    rows = ""
    for et in (1, 2, 3, 4):
        cards = "".join(_card(p, r["captain"]["id"], r["vice"]["id"])
                        for p in r["xi"] if p["element_type"] == et)
        rows += f'<div class="row">{cards}</div>'
    bench = "".join(_card(p, -1, -1) for p in r["bench"])

    d, m, f = r["formation"]
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    page = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>xSquad — GW{meta['gameweek']} optimal squad</title>
<style>{CSS}</style></head><body>
<header><h1>⚽ xSquad</h1><span>ILP-optimal FPL squad · rebuilt daily by GitHub Actions</span></header>
<main>
<div class="chips">
  <div class="chip">GW <b>{meta['gameweek']}</b></div>
  <div class="chip">Formation <b>{d}-{m}-{f}</b></div>
  <div class="chip">Cost <b>£{r['cost'] / 10:.1f}m</b> / £100.0m</div>
  <div class="chip">Ratings: <b>{html.escape(meta['rating_status'])}</b></div>
</div>
<div class="pitch">{banner}{rows}</div>
<h2>Bench (in order)</h2>
<div class="bench">{bench}</div>
<footer>Generated {now} · <a href="https://github.com/dhruvmehrish1527/xsquad">source on GitHub</a>
· Scoring: custom match-rating engine (backtested) + fixture ease + FPL xP</footer>
</main></body></html>"""

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(page)
    print(f"Wrote {OUT} — GW{meta['gameweek']}, {d}-{m}-{f}, £{r['cost'] / 10:.1f}m")


if __name__ == "__main__":
    main()
