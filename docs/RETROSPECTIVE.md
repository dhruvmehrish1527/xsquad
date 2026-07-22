# xSquad — Project Retrospective

**Project:** xSquad (formerly FPL Optimizer) — ILP-optimal Fantasy Premier League squad recommendations
**Period:** 2026-07-04 → 2026-07-08
**Author:** Dhruv Mehrish (product owner) with Claude (engineering)
**Companion documents:** [SRS v1.5](SRS.md) (IEEE 830-1998), [RATING_SPEC.md](fpl-optimizer/RATING_SPEC.md)

---

## 1. What was built

A local Python full-stack web app (FastAPI + HTMX + SQLite) that recommends a
mathematically optimal 15-man FPL squad each gameweek: exact ILP optimization (HiGHS)
under the official rules (£100.0m budget, 2/5/5/3 squad, max 3 per club, legal
formations, captain/vice/bench), squad import by FPL team ID, transfer plans with
−4-hit arithmetic, chip advice, a proprietary backtested match-rating engine, and
point projections calibrated to real FPL units.

Headline numbers: 841 players ingested and scored in ~1.5 s; ILP solves in ~0.1 s;
custom rating engine covers 508 players over 10,108 player-matches; its shipped signal
predicts next-gameweek points at Spearman **+0.178**, beating BPS (+0.147), FPL form
(+0.141), and ICT (+0.134) on a 33-gameweek backtest.

## 2. The process we followed

### 2.1 Requirements before code
The project began with a deliberately interrogative phase: two structured rounds of
questions (data sourcing, output scope, optimization method, platform, scoring
philosophy, squad state, rule depth, tech stack) before any design. This surfaced the
project's single biggest risk on day one — the requested "FotMob API" does not exist
as a licensed product — and let the product owner make an informed sourcing decision
rather than inherit a silent assumption.

### 2.2 A contractual, living SRS
An IEEE 830-1998 SRS was written and approved before implementation: numbered,
testable requirements (40+), a formal ILP model, a scoring specification, a
traceability matrix mapping every sentence of the original request to requirements,
and an explicit open-issues table requiring sign-off. Crucially, the SRS did not
fossilize: five change requests (CR-1…CR-5) were appended as the product evolved, each
recording what changed, why, and which requirements it amended. The document ended the
project still true.

### 2.3 Build in layers, verify each one
Implementation proceeded ingestion → scoring → optimizer → transfers/chips → UI, with
the optimizer verified by programmatic constraint assertions (budget, structure, club
cap, formation legality) against live data before any UI existed, and every UI change
verified in a real browser session (screenshots, DOM inspection, interaction tests)
rather than assumed.

### 2.4 Evidence gates for models
The defining process decision of the second half: **no model ships on plausibility.**
The custom rating engine was specified first (RATING_SPEC.md), implemented second, and
subjected to a predictive backtest with a pre-committed ship gate ("beats BPS on rank
correlation or it doesn't ship"). The gate writes an approval flag the app obeys — if a
future retune fails, the app silently falls back to the previous source chain.

## 3. Problems encountered and how they were solved

### P1 — The requested data source didn't exist
**Problem:** The brief assumed a "FotMob API." FotMob has no licensed API; its internal
endpoints require a signed `x-mas` header and are blocked in practice.
**Solution path:** (a) flagged during refinement, not discovered in production;
(b) built as a best-effort, throttled, cached module isolated behind an interface
(design constraint DC-03) with graceful degradation to FPL-only scoring; (c) when it
proved dead, the isolation made three successive source swaps (API-Football → BPS
fallback → custom engine) cheap — the optimizer, scoring blend, and UI never changed.
**Residual:** a community FotMob MCP wrapper was evaluated on request and rejected — it
passes a static token through without solving the signing problem.

### P2 — The replacement's free tier didn't cover the current season
**Problem:** API-Football was recommended and integrated, then its API rejected
current-season requests: the free plan only covers 2021–2023. The recommendation had
been made without verifying that restriction.
**Solution:** the mistake was owned explicitly, recorded in the SRS (CR-2 finding), and
the BPS fallback absorbed the gap — the app never went dark. The episode directly
motivated building a rating source we own end-to-end.

### P3 — The solver binary wouldn't run on the hardware
**Problem:** PuLP's bundled CBC solver is an Intel binary; the machine is Apple
Silicon ("Bad CPU type in executable").
**Solution:** swapped to HiGHS (native arm64 wheels) behind a one-line solver factory
with CBC as fallback. Solve times landed at ~0.1 s for the full 841-player ILP.

### P4 — The engine failed its own backtest
**Problem:** the custom rating engine v1.0 scored Spearman +0.068 vs BPS's +0.147 —
a decisive failure of the pre-committed gate.
**Diagnosis:** the z-score design measures *quality per minute* (minutes-prorated by
construction), but next-gameweek points are dominated by *playing volume* — a signal
raw BPS sums keep and the normalization deliberately stripped.
**Solution:** v1.1 multiplies rating form by recent-minutes share. Result: +0.178,
beating every baseline, winning 20/33 gameweeks vs BPS. The gate flipped to PASS and
the engine went live.
**Note:** the failure was reported to the product owner before the fix was attempted —
the gate's credibility depends on failures being visible.

### P5 — Never-played players filled the starting XI
**Problem:** after a weight change, nine of eleven starters were academy players with
zero career minutes, all scoring a flat 6.00.
**Diagnosis:** the "unavailable term redistributes its weight" rule — designed for
source outages — also fired per player. An unrated unknown lost the rating term and
inherited his club's fixture ease at full redistributed weight; absence of evidence
had become absence of penalty, and unknowns outscored Haaland.
**Solution:** the scorer now distinguishes *source down* (redistribute, as designed)
from *player unseen by a healthy source* (conservative prior ≈ a 5.7 rating:
"assume bench-warmer"). Verified: zero unrated players in the XI afterward. The same
mechanism doubles as the early-season shrinkage the rating spec prescribed.

### P6 — The projection number was in the wrong currency
**Problem:** "Projected XI: 65" was read against real FPL scores ("teams get 100+"),
but it was the optimizer's internal objective — unit-less, weight-dependent, and
incomparable across runs.
**Solution:** a least-squares calibration of blend score → actual next-GW points over
8,523 historical player-gameweeks (E[pts] = 1.94 + 0.675·score), displayed on the
pitch as "~68 FPL pts ±11" with a measured error band. The internal objective remains
available but clearly subordinate.

### P7 — Nobody knew what the best weights were
**Problem:** weight choices (rating vs fixture vs xP) were being made by intuition.
**Solution:** a grid sweep over the two historically testable terms found a clean
inverted-U: rating 0.6 / fixture 0.4 is optimal (+0.189), beating either signal alone
(~+0.14) by ~30%, with a flat plateau from 0.5–0.7. Recommended app weights
0.50/0.30/0.20 keep that ratio and give untestable xP a modest hedge. xP cannot be
backtested (FPL doesn't archive it) — a fact treated as an argument against
concentrating weight in it.

### P8 — Source health was invisible
**Problem:** the app degraded gracefully but silently; the owner couldn't see that
FotMob was dead.
**Solution:** per-source health surfaced directly in the weights panel — each weight
input turns amber (degraded) or red (no data) with a legend and hover detail, updated
out-of-band on every optimization run.

## 4. Learnings

1. **Interrogate the brief before honoring it.** The two question rounds changed the
   product materially (data sourcing, transfers, chips, formation control) and caught
   the nonexistent-API assumption at zero cost.
2. **A living SRS earns its keep through change, not stasis.** Five CRs later, the
   traceability matrix still answers "why is the code like this?" — the document was
   cheap insurance against scope amnesia.
3. **Isolate what you don't control.** Every external dependency (FotMob, API-Football,
   odds, even the solver) sat behind an interface. All of them changed; the core never did.
4. **Degrade gracefully, but never silently.** Fallbacks without surfaced health
   indicators erode trust precisely when they work best (P8).
5. **Pre-committed evidence gates beat post-hoc judgment.** The backtest gate failed
   its own model on first contact (P4) — that failure, reported honestly, is what makes
   the shipped +0.178 believable.
6. **Interrogate what a metric strips out.** Normalizing to per-minute quality removed
   the volume signal that actually predicts points; the best fix was multiplication,
   not a new model.
7. **Missing data has more than one meaning.** "Source down" and "player unknown"
   demanded opposite treatments (redistribute vs penalize); conflating them produced
   the project's most absurd output (P5).
8. **Ensembles beat any single signal — measurably.** The sweep's inverted-U (P7) is
   the diversification argument in one chart; it also justified refusing to put 100%
   weight on the one signal that can't be measured (xP).
9. **Speak to users in real units.** An internal objective leaking into the UI caused
   genuine confusion; calibration to points with an honest ±band resolved it (P6).
10. **Verify in the medium the user lives in.** Browser-level verification caught
    layout overflow, truncated inputs, and stale-server issues that unit-level checks
    never would.
11. **Own recommendation errors loudly.** The API-Football free-tier miss (P2) was
    documented in the SRS rather than buried — and directly motivated the project's
    best feature (the custom engine).
12. **Single-season fits are provisional.** The weight optimum and calibration are
    tuned on 2025-26 alone; both carry explicit instructions to re-validate on fresh
    2026-27 data before being trusted deep into next season.

## 5. Final state

| Component | State |
|---|---|
| Optimizer | Exact ILP (HiGHS), squad+XI+captain joint solve, formation auto/lock, ~0.1 s |
| Scoring | Match rating 0.50 / fixture ease 0.30 / FPL xP 0.20 (registry, per-source health) |
| Rating source | Custom engine v1.1 (backtest-gated, +0.178); fallbacks: API-Football → BPS |
| Projections | Calibrated to FPL points (±11 band) on the pitch banner |
| Squad state | Import by team ID; transfer plans net of −4 hits; chip advice |
| Docs | SRS v1.5 (+docx), RATING_SPEC.md, README, this retrospective |

**Standing maintenance:** weekly `--fetch` to fold in new matches; re-run `--sweep`
and `--calibrate` after weight changes and once 2026-27 has a few months of data;
re-run the backtest gate after any engine retune.

**Known future work:** FBref Tier-2 stats for sharper DEF/GK ratings; trailing-window
baselines to remove the v1 backtest's parameter-level leakage; fine-position emphasis
(CB vs FB) once Tier-2 lands; bench-boost-aware projection banner.
