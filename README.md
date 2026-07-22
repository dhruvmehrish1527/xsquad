# xSquad

ILP-optimal Fantasy Premier League squads each gameweek, per the approved
[SRS](docs/SRS.md) (IEEE 830-1998). (Formerly "FPL Optimizer"; renamed 2026-07-12.)
Project history and engineering decisions: [retrospective](docs/RETROSPECTIVE.md).

## About this project

I designed, specified, and directed this project — the requirements process,
data-source decisions (including rejecting an unlicensed scraping approach and
choosing to build a proprietary rating engine instead), the model philosophy,
scoring-weight choices, and the validation criteria — with **Claude (Anthropic)
as an AI pair-programmer for implementation**. Development was spec-driven: an
IEEE 830-1998 SRS was written and approved before any code, then maintained
through five change requests as the product evolved. Every predictive model had
to pass a pre-committed backtest gate before shipping — including one that
failed on its first attempt and had to be rethought. The full engineering
process, including the failures and how they were diagnosed and resolved, is
documented in [docs/RETROSPECTIVE.md](docs/RETROSPECTIVE.md).

## What it does

- Pulls prices, form, availability/injuries, fixtures and xP from the **official
  FPL public API**.
- Match ratings from our **custom rating engine** (`services/custom_rating.py`,
  spec in [RATING_SPEC.md](RATING_SPEC.md)): positional z-scores over official
  per-match raw stats (xG/xA/xGC/saves/defensive contributions), 6.0-baseline,
  volume-adjusted, last-5 recency-weighted. **Backtest-gated**: activated only
  because it out-predicts the alternatives on 2025-26 (Spearman +0.178 vs BPS
  +0.147, form +0.141, ICT +0.134). Reproduce with:
  `.venv/bin/python -m app.backtest` (populate history first via
  `.venv/bin/python -m app.services.custom_rating --fetch`).
  Fallback chain if the gate ever fails: API-Football (keyed) → FPL BPS.
  (FotMob was the original source; retired per CR-1, module dormant.)
- Scores every player with a rating-dominant blend (weights tunable in the UI).
- Solves an **exact ILP** (PuLP + HiGHS) for the best legal 15-man squad:
  £100.0m budget, 2/5/5/3 structure, max 3 per club, legal XI, captain/vice,
  ordered bench.
- **Formation:** Auto (solver picks the best shape) or Locked (e.g. 3-5-2 —
  biases the whole squad's spend toward starting positions).
- Imports your real squad by **FPL team ID** and suggests **transfers** with
  net gain after −4 hits, plus **chip advice** (WC / FH / BB / TC).

## Run

```bash
cd fpl-optimizer
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --port 8321
# open http://localhost:8321
```

## Layout

```
app/
  config.py            # weights, budget, legal formations
  db.py                # SQLite cache + squad history
  services/
    fpl_api.py         # official FPL API client (backbone)
    fotmob.py          # best-effort ratings, isolated + degradable
    matching.py        # fuzzy FotMob->FPL reconciliation
    scoring.py         # Appendix A scoring model
    optimizer.py       # Appendix B ILP (squad+XI+captain, formation modes)
    transfers.py       # squad import, transfer plans, chip advice
  main.py              # FastAPI routes
  templates/           # Jinja + HTMX UI (pitch view)
```

## Known limitations (documented deviations)

- Scoring uses three terms (match rating / fixture ease / FPL xP) per CR-5; ICT,
  fixture-ease, and bookmaker-odds terms were removed on user request. Fixture
  context still enters via the rating engine's opponent adjustment and
  blank-gameweek zeroing. Dormant modules (`api_football.py`, `odds_api.py`,
  `fotmob.py`) remain on disk but have no UI and no stored keys.
- Free-transfer count can't be read reliably from the public API; it defaults
  to 1 (adjustable).
