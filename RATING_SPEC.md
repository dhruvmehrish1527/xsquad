# Custom Match-Rating Engine — Specification v0.1

Design for `services/custom_rating.py`: an independent per-player per-match rating
(0–10 scale, baseline 6.0) computed from free raw event stats, replacing the BPS
pseudo-rating in the app's "Match rating" slot.

## 0. Data inputs

| Tier | Source | Stats used | Cadence |
|---|---|---|---|
| 1 | FPL `element-summary` (official) | minutes, starts, goals, assists, xG, xA, xGC, saves, penalties saved/missed, clean sheets, goals conceded, own goals, cards, defensive_contribution (CBIT/CBIRT) | 1 call/player, full season history; ~1 call/player/week incremental |
| 2 | FBref match logs (polite scrape, 1 req/3s) | shots, SoT, key passes, SCA, GCA, progressive passes/carries, passes into penalty area, crosses, take-ons, touches in att-pen, dispossessed, miscontrols, tackles (won), interceptions, blocks, clearances, recoveries, aerials won/lost, errors, fouls, pens conceded, offsides; GK: PSxG, saves, crosses stopped, sweeper actions; **actual position played** (CB/FB/DM/AM/W/ST) | ~20 team pages/GW |
| 3 | Understat (embedded JSON) | npxG, shot-level xA, xGChain, xGBuildup | ~20 pages/GW |

v1 runs on Tier 1 alone (reduced stat set); Tiers 2–3 upgrade precision when ingested.
Missing tiers degrade gracefully: their stats drop out of the category sums (same
redistribution philosophy as the app's scoring blend).

## 1. Core method: positional z-scores, not raw counts

Raw counts mislead (7 clearances is huge for a winger, routine for a CB). Every stat
`s` for player `i` in match `m` is converted to a **positional standardized surplus**:

```
z(s,i,m) = ( s_im − μ_pos(s) · mins_im/90 ) / σ_pos(s)        clamped to [−2.5, +2.5]
```

- `μ_pos(s)`, `σ_pos(s)`: league per-90 mean/SD of stat `s` for that **fine position**
  (GK, CB, FB, DM, CM, AM, W, ST), recomputed weekly from the trailing 10 GWs.
  Fine position comes from FBref when available, else FPL's GK/DEF/MID/FWD.
- Minutes-proration means a 20-minute cameo is judged against a 20-minute expectation,
  not a full-match one.
- Clamping stops one freak stat (14 clearances in a siege) from dominating.

## 2. Category contributions

Each category `c` aggregates its stats' z-scores with internal weights, then is capped:

```
C_c = clamp( Σ_s w_s · z(s), −cap_c, +cap_c )
```

### A. Finishing (cap ±1.6)
| stat | w | note |
|---|---|---|
| non-penalty goals | 0.55 | the outcome |
| npxG | 0.30 | the process — credits big chances even when missed |
| finishing delta (npG − npxG) | 0.15 | partial credit for clinical finishing; deliberately small (it's noisy) |
| shots on target | 0.10 | |
| penalty goal | flat +0.30 | less than open play — the chance was gifted |
| penalty missed | flat −0.45 | |

### B. Creation (cap ±1.4)
| stat | w |
|---|---|
| assists | 0.35 |
| xA | 0.30 |
| goal-creating actions (GCA) | 0.20 |
| shot-creating actions (SCA) | 0.15 |
| key passes | 0.15 |
| completed passes into penalty area | 0.10 |
| completed crosses | 0.05 |

### C. Progression & retention (cap ±1.0)
| stat | w |
|---|---|
| progressive carries + progressive passes | 0.30 |
| xGBuildup (Understat) | 0.20 |
| successful take-ons | 0.15 |
| touches in attacking penalty area | 0.15 |
| pass completion vs positional baseline | 0.10 |
| dispossessed + miscontrols | −0.20 |

### D. Defending (cap ±1.5; multiplied by position emphasis, §3)
| stat | w |
|---|---|
| tackles won + interceptions | 0.30 |
| blocks + clearances | 0.20 |
| recoveries | 0.15 |
| aerials won (net of lost) | 0.15 |
| defensive_contribution (FPL CBIT/CBIRT) | 0.20 — Tier-1 fallback when FBref absent |
| errors leading to shot | −0.40 |
| errors leading to goal | −0.80 |

### E. Team defensive outcome (cap ±0.9) — DEF/GK/DM only, minutes-shared
```
E = 0.45·CS_share + 0.35·(team xGC_baseline − xGC_actual)·share − 0.25·goals_conceded_while_on
```
Uses xGC so a keeper behind a sieve defence isn't punished for the defence.

### F. Goalkeeping (cap ±1.8; replaces A–C emphasis for GKs)
| stat | w |
|---|---|
| shot-stopping: PSxG − goals allowed | 0.45 — the single best GK skill metric |
| saves (volume) | 0.20 |
| penalty saved | flat +0.70 |
| crosses stopped + sweeper actions | 0.15 |
| launched-distribution completion vs baseline | 0.10 |
| Tier-1 fallback (no FBref): saves 0.35, xGC_delta 0.40, pens saved flat | |

### G. Discipline & negatives (no positive side; floor −1.2)
yellow −0.15 · second-yellow/red −0.90 (straight red −1.0) · own goal −0.70 ·
penalty conceded −0.50 · fouls z-surplus −0.10 · offsides z-surplus −0.05

## 3. Position emphasis matrix

Category totals are scaled per fine position before summing (rows ≈ "what the job is"):

| pos | A Finish | B Create | C Progress | D Defend | E TeamDef | F GK |
|-----|---------|----------|-----------|----------|-----------|------|
| GK  | 0 | 0.1 | 0.2 | 0.2 | 1.0 | 1.0 |
| CB  | 0.5 | 0.4 | 0.7 | 1.0 | 1.0 | — |
| FB  | 0.6 | 0.8 | 0.9 | 0.9 | 0.8 | — |
| DM  | 0.6 | 0.7 | 0.9 | 1.0 | 0.6 | — |
| CM  | 0.8 | 0.9 | 1.0 | 0.7 | 0.3 | — |
| AM/W| 1.0 | 1.0 | 0.9 | 0.4 | 0 | — |
| ST  | 1.0 | 0.8 | 0.6 | 0.3 | 0 | — |

(G Discipline applies at 1.0 to everyone.)

## 4. Context adjustments (multiplicative on the surplus, not the baseline)

```
raw_surplus = Σ_c emphasis(pos,c) · C_c  +  G
adjusted    = raw_surplus × opp_factor × minutes_factor  + home_nudge
```

- **Opponent strength** `opp_factor = 1 + 0.10·(opp_elo_z)`: outplaying City counts
  more than outplaying the bottom club. Elo proxy: rolling table position + FDR blend
  (Tier 1) or club Elo if ingested later. Bounded [0.85, 1.15].
- **Minutes factor**: `min(1, mins/60)^0.5` — cameos can still earn a rating but
  can't max out; sub-15-minute appearances get rated only if a scoring event occurred,
  else "no rating" (matches industry practice, keeps last-5 windows honest).
- **Home nudge**: −0.05 home / +0.05 away on the surplus (away performance is worth
  slightly more; kept tiny deliberately).
- **No result term.** Win/loss is deliberately excluded — team outcome already leaks
  in via category E, and result-chasing is how ratings get polluted by teammates.

## 5. Final assembly

```
rating_im = clamp( 6.0 + adjusted_surplus, 3.0, 10.0 )
```

Empirical sanity targets (validated in backtest): league mean ≈ 6.4–6.6, SD ≈ 0.55,
<1% of performances above 8.8, MOTM-type games 8.3+, disasters (red + OG) below 4.5.

**Form for the app's rating slot** (unchanged interface): last-5 recency-weighted
mean, weights 1.0/0.85/0.70/0.55/0.40, exposed as `fotmob_rating` so scoring.py,
matching, and the UI need zero changes. Panel label: "custom engine vX".

## 6. Calibration & backtest (before trusting it)

1. **Rescale z-weights once on history**: fit so that the surplus distribution hits the
   §5 sanity targets on last season's data.
2. **Predictive backtest**: for each GW k in the hold-out season, compute last-5 form
   ratings through k, correlate (Spearman) with GW k+1 FPL points, position-stratified.
   Compare against three baselines: BPS-per-90 (current fallback), FPL form, FPL xP.
   **Ship only if it beats BPS on rank correlation**; otherwise the extra complexity
   isn't earning its keep.
3. **Weight tuning**: coarse grid / coordinate descent on category caps and emphasis
   rows against the same objective, tuned on season N−1, evaluated on N (no peeking).
4. Store engine version + weights in the DB with each rating so historical ratings
   remain reproducible after retuning.

## 7. Failure modes to respect

- **Name matching** (FBref/Understat → FPL): reuse `matching.attach_field`; log
  sub-threshold matches for manual aliasing; expect ~3–5% needing alias entries.
- **FBref blocks/slowness**: engine must run Tier-1-only (reduced stat set, wider
  category caps) — never dark.
- **Early season**: until a player has 3 rated matches, blend his rating toward his
  positional mean (shrinkage: `(n·r̄ + 3·μ_pos)/(n+3)`) so August isn't decided by
  one game.
- **Double GWs**: rate each match separately; the form aggregator already handles
  multiple entries.
