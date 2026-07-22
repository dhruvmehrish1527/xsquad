# Software Requirements Specification
## xSquad — Optimal Premier League Fantasy Team Generator

*(Product formerly named "FPL Optimizer"; renamed **xSquad** 2026-07-12. Body text
retains the original name where historically accurate.)*

**Document version:** 1.5
**Date:** 2026-07-04
**Prepared for:** Dhruv Mehrish
**Standard:** IEEE Std 830-1998 (Recommended Practice for Software Requirements Specifications)
**Status:** Approved (2026-07-04) — implementation authorized

**Revision history**

| Ver | Date | Change |
|-----|------|--------|
| 1.0 | 2026-07-04 | Initial approved baseline. |
| 1.1 | 2026-07-04 | CR-1 (Appendix F): primary match-rating source changed from FotMob (unlicensed, found blocked in practice) to **API-Football** (licensed, free tier) with an **FPL BPS pseudo-rating fallback**. Amends SW-02, FR-DATA-03/04/06, C-5, Appendix A FotMobForm term. |
| 1.2 | 2026-07-04 | CR-2 (Appendix F): scoring model generalized to a **dynamic source registry**; added **FPL ICT index** and **bookmaker anytime-scorer odds** (The Odds API, keyed) as weighted terms. Per-player unavailable terms redistribute weight pro-rata. Amends Appendix A formula, FR-SCORE-04, UI-05. Records the API-Football free-tier season restriction discovered at integration. |
| 1.3 | 2026-07-04 | CR-3 (Appendix F): **custom match-rating engine v1.1** (per RATING_SPEC.md) becomes the primary rating source — positional z-score model over official per-match raw stats (xG/xA/xGC/saves/defensive contributions), volume-adjusted, **backtest-gated** (Spearman +0.178 vs BPS +0.147 on 2025-26 hold-out; activation controlled by the gate flag). Source chain: custom engine → API-Football → BPS. |
| 1.4 | 2026-07-08 | CR-4 (Appendix F): scoring registry trimmed to **match rating / FPL form / FPL xP** — ICT index, fixture-ease, and scorer-odds terms removed at product-owner request; API key management (API-Football, The Odds API) removed from the UI and stored keys deleted. Fixture context is retained via the rating engine's opponent-difficulty adjustment and blank-GW zeroing (O-5). Partially reverses CR-2. |
| 1.5 | 2026-07-08 | CR-5 (Appendix F): **fixture-ease term restored; FPL form term removed** at product-owner request. Registry: match rating (0.50) / fixture ease (0.30) / FPL xP (0.20). Restores FDR-based opponent weighting and double-GW upweighting (O-5) to the blend. |

---

## Table of Contents

1. [Introduction](#1-introduction)
   1. [Purpose](#11-purpose)
   2. [Scope](#12-scope)
   3. [Definitions, Acronyms, and Abbreviations](#13-definitions-acronyms-and-abbreviations)
   4. [References](#14-references)
   5. [Overview](#15-overview)
2. [Overall Description](#2-overall-description)
   1. [Product Perspective](#21-product-perspective)
   2. [Product Functions](#22-product-functions)
   3. [User Characteristics](#23-user-characteristics)
   4. [Constraints](#24-constraints)
   5. [Assumptions and Dependencies](#25-assumptions-and-dependencies)
   6. [Apportioning of Requirements](#26-apportioning-of-requirements)
3. [Specific Requirements](#3-specific-requirements)
   1. [External Interface Requirements](#31-external-interface-requirements)
   2. [Functional Requirements](#32-functional-requirements)
   3. [Performance Requirements](#33-performance-requirements)
   4. [Design Constraints](#34-design-constraints)
   5. [Software System Attributes](#35-software-system-attributes)
   6. [Other Requirements](#36-other-requirements)
4. [Appendices](#4-appendices)
   1. [Appendix A: Scoring Model Specification](#appendix-a-scoring-model-specification)
   2. [Appendix B: Optimization Model (ILP) Specification](#appendix-b-optimization-model-ilp-specification)
   3. [Appendix C: Analysis Models](#appendix-c-analysis-models)
   4. [Appendix D: Requirements Traceability Matrix](#appendix-d-requirements-traceability-matrix)
   5. [Appendix E: Open Issues](#appendix-e-open-issues)

---

## 1. Introduction

### 1.1 Purpose

This Software Requirements Specification (SRS) defines the functional and non-functional
requirements for **FPL Optimizer**, a web application that recommends the mathematically
optimal Fantasy Premier League (FPL) squad for each gameweek, subject to the official rules
and budget of the game.

The intended audience is: the product owner/end user (the author of the originating request),
the developer(s) implementing the system, and any reviewer validating that the delivered
software meets the agreed requirements. This document is the contractual basis for
implementation and acceptance; coding begins only after it is approved.

### 1.2 Scope

**Product name:** FPL Optimizer.

**What the software will do:**

- Ingest Premier League player data each gameweek from the **official FPL public API**
  (prices, positions, clubs, form, injury/availability status, fixtures, expected points,
  and ownership).
- Ingest **FotMob player match ratings** on a best-effort basis via FotMob's internal
  (undocumented) endpoints, to serve as the *dominant* quality signal in the scoring model.
- Compute a single **projected-points score** per player per gameweek by combining, in
  priority order, FotMob recent match ratings, FPL recent form, fixture difficulty, and
  availability (see [Appendix A](#appendix-a-scoring-model-specification)).
- Solve, via **Integer Linear Programming (ILP)**, for the squad that maximizes total
  projected points subject to all official FPL constraints:
  - Total price ≤ **£100.0m** budget.
  - Exactly **15 players**: 2 goalkeepers, 5 defenders, 5 midfielders, 3 forwards.
  - **Maximum 3 players per Premier League club.**
  - A legal **starting XI** (valid formation), plus **captain** and **vice-captain**, plus
    a **bench order** for the remaining 4.
- **Import the user's existing squad** from their public FPL team ID and, from gameweek to
  gameweek, produce **transfer suggestions** that respect the free-transfer allowance and the
  −4 point penalty per additional transfer.
- Provide **chip advice**: recommend when to play Wildcard, Free Hit, Bench Boost, and Triple
  Captain based on projected gains.
- Present all of the above through a **web user interface** (players on a pitch, prices,
  projected scores, and suggested swaps), backed by a Python service that runs the solver.

**What the software will NOT do (out of scope for v1.0):**

- It will not automatically submit or make transfers on the user's real FPL account (advisory
  only; no write access to the FPL account).
- It will not guarantee real-world points outcomes; all outputs are projections.
- It will not provide betting, odds, or wagering functionality.
- It will not support fantasy formats other than the official Premier League Fantasy game.
- It will not redistribute or expose raw FotMob data beyond what is needed to display a
  player's rating within the app.

**Benefits / objectives:** reduce the manual research burden of picking a squad each gameweek,
and produce a defensible, rules-compliant, budget-constrained recommendation that is at least
as good as (and typically better than) an unaided manual selection under the chosen scoring
model.

### 1.3 Definitions, Acronyms, and Abbreviations

| Term | Definition |
|------|------------|
| **FPL** | Fantasy Premier League — the official fantasy game at fantasy.premierleague.com. |
| **Gameweek (GW)** | A round of Premier League fixtures forming one FPL scoring period. |
| **Squad** | The full set of 15 selected players. |
| **Starting XI** | The 11 players who score points in a gameweek; the other 4 are the bench. |
| **Formation** | The distribution of the starting XI across DEF/MID/FWD (GK always 1). Legal FPL formations require exactly 1 GK, 3–5 DEF, 2–5 MID, 1–3 FWD, totaling 11. |
| **Captain (C)** | Player whose points are doubled for the gameweek. |
| **Vice-captain (VC)** | Player who receives the captain multiplier if the captain does not play. |
| **Budget** | The spending cap for the 15-man squad: £100.0m (represented internally as 1000 tenths-of-a-million). |
| **FDR** | Fixture Difficulty Rating — a measure of how hard a player's upcoming opponent is. |
| **xP** | Expected Points — FPL's own model of a player's projected points. |
| **ICT Index** | FPL's Influence, Creativity, Threat composite metric. |
| **FotMob rating** | A per-match performance rating (approx. 0–10 scale) published by FotMob. |
| **Chip** | A single-use FPL power: Wildcard, Free Hit, Bench Boost, Triple Captain. |
| **Free transfer (FT)** | A transfer that incurs no points penalty; normally 1 per GW, bankable up to a capped maximum. |
| **Hit** | A −4 point penalty applied for each transfer beyond the free-transfer allowance. |
| **ILP** | Integer Linear Programming — the optimization technique used to select the squad. |
| **Team ID** | The public numeric identifier of a user's FPL entry, used to fetch their squad. |
| **ToS** | Terms of Service. |

### 1.4 References

1. IEEE Std 830-1998, *IEEE Recommended Practice for Software Requirements Specifications.*
2. Official Fantasy Premier League rules — https://fantasy.premierleague.com/help/rules
3. FPL public API (undocumented but public) — `https://fantasy.premierleague.com/api/`
   (e.g. `bootstrap-static/`, `fixtures/`, `entry/{team_id}/`, `element-summary/{id}/`).
4. FotMob — `https://www.fotmob.com/` (internal endpoints under `/api/`; no official
   developer API or license).
5. Originating user request (2026-07-04) and two rounds of refinement captured in
   [Appendix E](#appendix-e-open-issues) / decision log below.

### 1.5 Overview

Section 2 gives a high-level description of the product, its context, principal functions,
users, constraints, and dependencies. Section 3 states the detailed, verifiable requirements
(interfaces, functional behavior, performance, and quality attributes). The appendices define
the scoring formula, the formal optimization model, analysis diagrams, a traceability matrix,
and outstanding decisions.

---

## 2. Overall Description

### 2.1 Product Perspective

FPL Optimizer is a new, self-contained application that runs primarily on the user's local
machine (a Python full-stack web app served locally). It is **not** part of, endorsed by, or
integrated (for writes) with the official FPL platform. It depends on two external data
sources:

- **FPL public API** — treated as the reliable, authoritative backbone for prices, structure,
  form, availability, fixtures, and the user's current squad.
- **FotMob internal endpoints** — treated as a *best-effort* enrichment source for player
  match ratings. Because FotMob provides no official API, the system must degrade gracefully
  when FotMob data is unavailable (see FR-DATA-06 and the constraint in 2.4).

System context:

```
        +-------------------+           +--------------------+
        |   FPL Public API  |           |  FotMob Endpoints  |
        | (prices, form,    |           | (match ratings)    |
        |  fixtures, squad) |           |  best-effort only  |
        +---------+---------+           +----------+---------+
                  |                                |
                  v                                v
        +--------------------------------------------------+
        |             FPL Optimizer Backend (Python)       |
        |  Ingestion -> Scoring Model -> ILP Optimizer     |
        |  -> Transfer/Chip Advisor -> Local Database      |
        +-----------------------+--------------------------+
                                |
                                v
                    +-----------------------+
                    |   Web UI (browser)    |
                    |  pitch, prices, swaps |
                    +-----------------------+
```

The backend exposes an HTTP/JSON API consumed by the web front end. Persistent state
(cached data, user's team ID, historical squads, tunable weights) is stored in a local
database.

### 2.2 Product Functions

At a summary level, the system shall:

1. **Fetch and cache** gameweek data from the FPL API and (best-effort) FotMob.
2. **Score** every eligible player with a single projected-points value.
3. **Optimize** a legal, budget-compliant 15-man squad plus XI/captain/bench.
4. **Import** the user's real squad by team ID and store squad history.
5. **Advise transfers** week to week, accounting for free transfers and −4 hits.
6. **Advise chips** (Wildcard, Free Hit, Bench Boost, Triple Captain).
7. **Present** results in a web UI and allow the user to tune scoring weights and re-run.

### 2.3 User Characteristics

- **Primary user:** a single FPL manager (the product owner), technically comfortable enough
  to run a local web app and enter their FPL team ID. Understands FPL rules. Expects clear,
  actionable recommendations and the ability to tweak model weights.
- No administrative or multi-tenant user roles are required in v1.0 (single-user app).

### 2.4 Constraints

- **C-1 (Budget):** Total squad price must not exceed £100.0m.
- **C-2 (Squad structure):** Exactly 2 GK, 5 DEF, 5 MID, 3 FWD.
- **C-3 (Club cap):** At most 3 players from any single Premier League club.
- **C-4 (Legal XI):** Starting XI must satisfy 1 GK, 3–5 DEF, 2–5 MID, 1–3 FWD = 11. By
  default the optimizer chooses the best legal formation; the user may optionally **lock** a
  specific formation (see FR-OPT-05), in which case the XI must match the locked shape exactly.
- **C-5 (FotMob legality/reliability):** FotMob has **no official API or license**. Use of its
  internal endpoints may violate FotMob's ToS and may be rate-limited or blocked without
  notice. The system must (a) throttle and cache requests conservatively, (b) never hard-fail
  when FotMob is unavailable, and (c) keep FotMob data confined to internal scoring and
  single-player display.
- **C-6 (Advisory only):** The system must not perform writes to the user's FPL account.
- **C-7 (Language/stack):** Backend in Python (required for the ILP solver); delivered as a
  Python full-stack web app.
- **C-8 (Prices in tenths):** FPL prices are in units of £0.1m; the optimizer must use integer
  tenths to avoid floating-point budget errors.

### 2.5 Assumptions and Dependencies

- **A-1:** The FPL public API remains reachable and backward-compatible in its current shape.
- **A-2:** FotMob player ratings can be matched to FPL players by name/club/position with an
  acceptable match rate; unmatched players fall back to FPL-only scoring.
- **A-3:** The user can supply a valid public FPL team ID.
- **A-4:** The user runs the app in an environment with outbound internet access and Python
  installed.
- **A-5:** "£100,000,000 budget" in the original request corresponds to the FPL in-game
  £100.0m cap; the "$" was informal.
- **Dependency:** An ILP solver library (e.g., PuLP with CBC, or OR-Tools) is available.

### 2.6 Apportioning of Requirements

Requirements marked **[v1.0]** are in the initial release. Requirements marked **[future]**
are desirable but deferred (e.g., multi-user accounts, mobile-native UI, automated
transfer execution, backtesting dashboards).

---

## 3. Specific Requirements

Each requirement has a unique ID and is stated to be testable. Priority: **M** = Mandatory,
**S** = Should, **C** = Could.

### 3.1 External Interface Requirements

#### 3.1.1 User Interfaces

- **UI-01 (M):** A web UI shall render the recommended 15-man squad, visually separating the
  starting XI (on a pitch) from the 4 bench players (ordered), and shall mark the captain (C)
  and vice-captain (VC).
- **UI-02 (M):** For each player the UI shall display: name, club, position, FPL price,
  projected-points score, FotMob recent rating (or "N/A" if unavailable), FPL form, next
  opponent, and availability/injury flag.
- **UI-03 (M):** The UI shall display total squad cost, remaining budget, and per-club counts,
  and shall visibly warn if any constraint is (or would be) violated.
- **UI-04 (M):** The UI shall provide an input for the user's FPL team ID and a control to
  import/refresh the current squad.
- **UI-05 (S):** The UI shall let the user adjust scoring weights (see Appendix A) and re-run
  the optimization without restarting the app.
- **UI-06 (M):** The UI shall present the transfer suggestions for the upcoming gameweek,
  including projected point gain and any −4 hit cost, and the chip recommendation (if any).
- **UI-07 (M):** The UI shall provide a formation control offering **Auto** (default) or a
  **Locked** formation chosen from the legal formations only, and shall re-run the optimization
  on change (supports FR-OPT-05/FR-OPT-06). When Locked is active, the pitch view shall render
  the XI in the selected shape.

#### 3.1.2 Hardware Interfaces

- **HW-01:** None beyond a standard personal computer capable of running Python and a modern
  web browser. No specialized hardware required.

#### 3.1.3 Software Interfaces

- **SW-01 (M):** The system shall consume the FPL public API endpoints, at minimum:
  `bootstrap-static/` (players, teams, prices, form, availability, positions, events),
  `fixtures/` (fixtures and difficulty), `entry/{team_id}/` and
  `entry/{team_id}/event/{gw}/picks/` (user squad), and `element-summary/{id}/` (per-player
  history/fixtures).
- **SW-02 (M):** The system shall consume FotMob internal endpoints to retrieve player match
  ratings, subject to constraint C-5, with request throttling and local caching.
- **SW-03 (M):** The backend shall expose an internal HTTP/JSON API to the front end for:
  data refresh, optimization run, squad import, transfer advice, and weight configuration.
- **SW-04 (M):** A local persistent store (e.g., SQLite) shall hold cached external data,
  the user's team ID, historical squads per gameweek, and scoring weights.

#### 3.1.4 Communications Interfaces

- **COM-01 (M):** All external data retrieval shall use HTTPS.
- **COM-02 (M):** Outbound requests shall send a reasonable request rate and identify a
  configurable User-Agent; the system shall honor HTTP 429/backoff signals.

### 3.2 Functional Requirements

#### 3.2.1 Data Ingestion

- **FR-DATA-01 (M):** The system shall fetch the current gameweek's player universe from the
  FPL API, including price (in tenths), position, club, form, availability/injury status, and
  the player's upcoming fixture(s) and difficulty.
- **FR-DATA-02 (M):** The system shall fetch fixtures and derive a per-player fixture-difficulty
  value for the target gameweek.
- **FR-DATA-03 (M):** The system shall fetch FotMob match ratings for players on a best-effort
  basis and compute a recent-form rating (e.g., a weighted average of the last N matches).
- **FR-DATA-04 (M):** The system shall reconcile FotMob players to FPL players (by name/club/
  position) and record the match confidence; unmatched players are flagged FotMob-N/A.
- **FR-DATA-05 (M):** The system shall cache all fetched data locally with a timestamp and
  shall not re-fetch within a configurable freshness window unless the user forces a refresh.
- **FR-DATA-06 (M):** If FotMob data is unavailable, stale, or unmatched for a player, the
  system shall degrade gracefully to FPL-only scoring for that player and clearly indicate
  the fallback in the UI (satisfying C-5).

#### 3.2.2 Player Scoring

- **FR-SCORE-01 (M):** The system shall compute exactly one **projected-points score** per
  eligible player per gameweek using the model in [Appendix A](#appendix-a-scoring-model-specification),
  in which the **FotMob recent rating is the dominant term**, adjusted by FPL form, fixture
  difficulty, and availability.
- **FR-SCORE-02 (M):** A player flagged unavailable/injured/suspended (or with low chance of
  playing) shall have their score reduced proportionally to availability, down to effectively
  zero when ruled out, so the optimizer avoids them.
- **FR-SCORE-03 (M):** Price shall be treated as a **cost/constraint input**, not as a term
  that inflates the projected score. (Per the original request, price reflects prior-season
  quality but must be spent within budget; the optimizer trades score against price.)
- **FR-SCORE-04 (S):** Scoring weights shall be externally configurable (config + UI) without
  code changes, and the system shall recompute scores on demand.

#### 3.2.3 Squad Optimization

- **FR-OPT-01 (M):** The system shall select the 15-man squad that **maximizes total projected
  points** subject to constraints C-1 through C-3, formalized as the ILP in
  [Appendix B](#appendix-b-optimization-model-ilp-specification).
- **FR-OPT-02 (M):** The system shall choose a legal starting XI (constraint C-4) maximizing
  projected points among the 15, designate the highest-projected eligible starter as
  **captain** and the next as **vice-captain**, and order the bench by projected points.
- **FR-OPT-03 (M):** The optimizer shall guarantee a true optimum (not a heuristic
  approximation) whenever a feasible solution exists; if infeasible, it shall report why
  (e.g., budget too low for a valid squad).
- **FR-OPT-04 (S):** The optimizer shall optionally maximize an objective that weights the
  captain's projected points at ×2 to reflect the captaincy multiplier.
- **FR-OPT-05 (M):** The system shall support two formation modes: **(a) Auto** (default) — the
  optimizer selects the best legal formation per FR-OPT-02; and **(b) Locked** — the user
  selects a specific legal formation (e.g., 3-4-3, 3-5-2, 4-4-2, 4-3-3, 5-3-2, etc.), and the
  starting XI must match that exact shape. The user may switch modes and re-run without
  restarting. Only legal FPL formations shall be selectable, and the system shall reject or
  hide illegal ones.
- **FR-OPT-06 (M):** When a formation is locked, the optimizer shall **bias the entire 15-man
  squad** toward that shape while still satisfying the mandatory 2/5/5/3 squad structure (C-2).
  Concretely, squad selection and XI selection shall be solved jointly against the locked
  formation, so budget is preferentially allocated to the positions that will field starters
  (e.g., under a locked 3-5-2 the two non-starting defenders may be minimum-price bench
  players, freeing budget for midfielders). Bench players shall be the lowest-cost legal
  choices consistent with squad legality unless Bench Boost optimization (FR-CHIP-03) is
  active.

#### 3.2.4 Squad Import & State

- **FR-STATE-01 (M):** Given a valid FPL team ID, the system shall import the user's current
  15-man squad, bank/budget, and free-transfer count from the FPL public API.
- **FR-STATE-02 (M):** The system shall persist the user's squad per gameweek to enable
  week-to-week comparison and transfer advice.
- **FR-STATE-03 (M):** If import fails or no team ID is provided, the system shall still allow
  a from-scratch optimal squad to be generated and used as the baseline.

#### 3.2.5 Transfer Advice

- **FR-TRANSFER-01 (M):** The system shall compare the user's current squad against the
  projected-optimal squad and produce a ranked list of suggested transfers (out/in pairs) for
  the upcoming gameweek.
- **FR-TRANSFER-02 (M):** Transfer suggestions shall respect the free-transfer allowance and
  compute the net projected gain **after** subtracting any −4 hit for each transfer beyond the
  allowance; the system shall not recommend a transfer whose net projected gain is negative
  unless explicitly requested.
- **FR-TRANSFER-03 (M):** All suggested post-transfer squads must remain within budget and
  satisfy constraints C-1 through C-4.

#### 3.2.6 Chip Advice

- **FR-CHIP-01 (M):** The system shall recommend when to play **Wildcard** (unlimited free
  transfers) based on the projected gain of a full re-optimization exceeding cumulative hit
  costs.
- **FR-CHIP-02 (M):** The system shall recommend when to play **Free Hit** based on projected
  one-week gain for a temporary optimal squad.
- **FR-CHIP-03 (M):** The system shall recommend **Bench Boost** in gameweeks where the bench's
  combined projected points are highest.
- **FR-CHIP-04 (M):** The system shall recommend **Triple Captain** in gameweeks where the
  captain's projected points (and favorable fixture) are highest.
- **FR-CHIP-05 (S):** Chip advice shall be advisory and never assume more than one chip is
  played in the same gameweek (per FPL rules).

#### 3.2.7 Presentation & Configuration

- **FR-UI-01 (M):** The system shall render all optimization outputs in the web UI per UI-01
  through UI-06.
- **FR-UI-02 (S):** The system shall allow the user to export the recommended squad and
  transfer plan (e.g., JSON/CSV).
- **FR-CFG-01 (S):** The system shall expose configuration for freshness window, scoring
  weights, target gameweek, and the "recent matches" window N for FotMob form.

### 3.3 Performance Requirements

- **PERF-01 (M):** With data already cached, a full optimization run (score + ILP + XI/captain
  selection) shall complete in **≤ 10 seconds** on a typical personal computer.
- **PERF-02 (M):** A cold data refresh (FPL + best-effort FotMob for the full player universe)
  shall complete in **≤ 3 minutes**, subject to external API latency and rate limits.
- **PERF-03 (S):** The web UI shall render the recommended squad within **≤ 2 seconds** of
  receiving the backend response.

### 3.4 Design Constraints

- **DC-01 (M):** Backend implemented in Python; ILP via an established solver (PuLP/CBC or
  OR-Tools).
- **DC-02 (M):** Prices and budget handled as integer tenths of a million (C-8).
- **DC-03 (M):** External-source access layer must be isolated behind an interface so FotMob
  can be disabled/replaced without affecting the optimizer (supports C-5, FR-DATA-06).
- **DC-04 (S):** Configuration and secrets (e.g., team ID) stored locally, not hard-coded.

### 3.5 Software System Attributes

- **REL-01 (Reliability, M):** Failure of the FotMob source shall not cause overall failure;
  the system remains usable on FPL data alone.
- **AVL-01 (Availability, S):** The app is single-user/local; availability equals the user's
  ability to run it. No uptime SLA.
- **SEC-01 (Security, M):** No credentials to the user's FPL account are collected or stored;
  only the public team ID is used. Cached third-party data is stored locally only.
- **MNT-01 (Maintainability, M):** Ingestion, scoring, optimization, and advice are separated
  into distinct modules with defined interfaces to allow independent change.
- **POR-01 (Portability, S):** Runs on macOS/Linux/Windows with Python and a modern browser.
- **USE-01 (Usability, S):** A first-time user can produce a recommended squad in ≤ 3 actions
  (enter team ID → refresh data → run optimization), or in ≤ 2 actions from cache.
- **LEG-01 (Legal/Compliance, M):** The system shall respect rate limits and shall keep FotMob
  data usage confined to internal scoring/single-player display (C-5); it shall not
  redistribute bulk FotMob data.

### 3.6 Other Requirements

- **OTH-01 (S):** The system shall log each optimization run (inputs, chosen squad, objective
  value) to support reproducibility and later backtesting **[future extension]**.

---

## 4. Appendices

### Appendix A: Scoring Model Specification

The projected-points score for player *i* in the target gameweek is a **FotMob-dominant**
blend. Each component is normalized to a comparable scale (e.g., 0–1) before weighting.

```
Score_i = Availability_i × (
            w_fotmob   × FotMobForm_i          # DOMINANT term
          + w_fplform  × FPLForm_i
          + w_fixture  × FixtureEase_i
          + w_xp       × FPL_xP_i              # optional supporting term
          )
```

Where:

- **FotMobForm_i** — weighted average of player *i*'s FotMob match ratings over the last *N*
  matches (default N = 5), recency-weighted; normalized. This is the **dominant** signal, so by
  default **w_fotmob is the largest weight** (e.g., default weights w_fotmob = 0.50,
  w_fplform = 0.25, w_fixture = 0.15, w_xp = 0.10 — all user-tunable per FR-SCORE-04).
- **FPLForm_i** — FPL's rolling form value, normalized.
- **FixtureEase_i** — derived from FDR for the upcoming fixture; easier opponents score higher
  (accounts for the player's opponent, per the original request).
- **FPL_xP_i** — FPL's own expected-points value, normalized (supporting term).
- **Availability_i** — a multiplier in [0, 1] from FPL availability/chance-of-playing and
  injury/suspension flags (per FR-SCORE-02); a ruled-out player → ~0.

Notes:
- If FotMobForm_i is unavailable (FR-DATA-06), its weight is redistributed to the remaining
  terms and the player is flagged FotMob-N/A.
- **Price is not part of Score.** Price enters only as a constraint/cost in Appendix B
  (per FR-SCORE-03).

### Appendix B: Optimization Model (ILP) Specification

**Sets**
- `P` = players; `C` = clubs; positions GK, DEF, MID, FWD.

**Parameters**
- `s_i` = projected score of player *i* (Appendix A).
- `c_i` = price of player *i* in integer tenths of £1m.
- `club(i)` = club of player *i*; `pos(i)` = position of player *i*.
- `B` = 1000 (i.e., £100.0m in tenths).

**Decision variables**
- `x_i ∈ {0,1}` — player *i* selected in the 15-man squad.
- `y_i ∈ {0,1}` — player *i* in the starting XI (`y_i ≤ x_i`).
- `z_i ∈ {0,1}` — player *i* is captain (`z_i ≤ y_i`, `Σ z_i = 1`).

**Objective (squad selection, captaincy-aware per FR-OPT-04)**
```
maximize  Σ_i s_i · y_i  +  Σ_i s_i · z_i        (captain counted twice ⇒ ×2)
```
(The bench contributes to squad legality/robustness; a bench-inclusive objective variant is
used for Bench Boost gameweeks.)

**Constraints**
```
Σ_i c_i · x_i ≤ B                                  # C-1 budget
Σ_i x_i = 15                                        # squad size
Σ_{pos(i)=GK}  x_i = 2                              # C-2 structure
Σ_{pos(i)=DEF} x_i = 5
Σ_{pos(i)=MID} x_i = 5
Σ_{pos(i)=FWD} x_i = 3
Σ_{club(i)=k}  x_i ≤ 3     ∀ k ∈ C                 # C-3 club cap
Σ_i y_i = 11                                        # starting XI size (C-4)
Σ_{pos(i)=GK,  y}=1 ; 3 ≤ Σ_{DEF,y} ≤ 5 ;
2 ≤ Σ_{MID,y} ≤ 5 ; 1 ≤ Σ_{FWD,y} ≤ 3              # legal formation (Auto mode)
y_i ≤ x_i ∀ i ; z_i ≤ y_i ∀ i ; Σ_i z_i = 1
```

**Formation lock (FR-OPT-05/06).** In **Auto** mode the formation inequalities above apply and
the solver picks the best legal shape. In **Locked** mode the three range inequalities are
replaced by exact equalities for the chosen shape, e.g. for 3-5-2:
```
Σ_{DEF,y} = 3 ; Σ_{MID,y} = 5 ; Σ_{FWD,y} = 2      # locked formation (GK y = 1 always)
```
Because squad selection (`x`) and XI selection (`y`) are optimized jointly with `y_i ≤ x_i`,
locking the shape automatically **biases the whole 15-man squad** (FR-OPT-06): the mandatory
2/5/5/3 counts still hold, but non-starting slots in over-provisioned positions are driven to
minimum-cost players so budget flows to the starting positions. This bias is emergent from the
joint objective and requires no separate constraint.

**Transfer variant (FR-TRANSFER):** add binary transfer variables and a term
`− 4 · max(0, (#transfers − FT))` to the objective, holding the current squad fixed except for
swaps, so the model maximizes net projected gain after hits.

The problem is solved with an exact ILP solver (FR-OPT-03); infeasibility is reported with the
binding constraint.

### Appendix C: Analysis Models

**Primary data flow (DFD level 0):**

```
[FPL API] --prices/form/fixtures/squad--> (Ingestion) --> (Cache/DB)
[FotMob]  --ratings (best-effort)-------> (Ingestion) --> (Cache/DB)
(Cache/DB) --> (Scoring Model) --scores--> (ILP Optimizer) --squad-->
   (Transfer & Chip Advisor) --> (Web API) --> (Web UI) --> [User]
[User] --team ID--> (Web API) --> (Squad Import) --> (Cache/DB)
```

**Key use cases:**
1. *Generate optimal squad for GW* (FR-OPT-01/02).
2. *Import my squad and get transfer plan* (FR-STATE-01, FR-TRANSFER-01/02).
3. *Tune weights and re-run* (FR-SCORE-04, UI-05).
4. *Get chip advice* (FR-CHIP-01..04).

### Appendix D: Requirements Traceability Matrix

| Origin (user request) | Requirement(s) |
|---|---|
| "Optimal FPL team each gameweek" | FR-OPT-01, FR-OPT-02, PERF-01 |
| "Combine FotMob ratings … and recent form" | FR-DATA-03, FR-SCORE-01, Appendix A |
| "Fits the budget of £100m" | C-1, FR-OPT-01, DC-02 |
| "Account for recent injuries" | FR-DATA-01, FR-SCORE-02 |
| "Opponents of players" | FR-DATA-02, FixtureEase term (Appendix A) |
| "Current form each gameweek" | FR-DATA-03, FR-SCORE-01 |
| "Official price … assess each option" | FR-DATA-01, FR-SCORE-03, Appendix B |
| "Max 3 players from each PL team" | C-3, FR-OPT-01, Appendix B |
| Refinement: FotMob dominant | FR-SCORE-01, Appendix A weights |
| Refinement: import from FPL account | FR-STATE-01 |
| Refinement: transfer suggestions | FR-TRANSFER-01..03 |
| Refinement: chips | FR-CHIP-01..05 |
| Refinement: web app, Python full-stack | UI-01.., DC-01, C-7 |
| Refinement: changeable formation (auto + lock, bias squad) | FR-OPT-05, FR-OPT-06, UI-07, C-4, Appendix B (formation lock) |

### Appendix E: Open Issues

All issues resolved and approved by the product owner on 2026-07-04:

| # | Issue | Resolution |
|---|---|---|
| O-1 | Exact default scoring weights | **Approved:** w_fotmob 0.50 / w_fplform 0.25 / w_fixture 0.15 / w_xp 0.10, all tunable. |
| O-2 | FotMob "recent matches" window N | **Approved:** N = 5, recency-weighted. |
| O-3 | UI framework within Python full-stack | **Approved:** FastAPI + server-rendered templates with HTMX. |
| O-4 | Local database | **Approved:** SQLite. |
| O-5 | Handling multi-fixture gameweeks (double/blank GWs) | **Approved:** Sum projected scores across a player's fixtures in that GW. |

---

### Appendix F: Change Requests

**CR-1 (approved 2026-07-04): Replace FotMob with API-Football + FPL BPS fallback.**

*Rationale:* FotMob's internal endpoints (constraint C-5) proved blocked in practice at
delivery; the product owner requires a dependable per-player per-match rating.

*Change:* The "match rating" signal is now sourced as follows, in priority order:

1. **API-Football (api-sports.io)** — licensed API, free tier (100 req/day, ~10 req/min).
   Per-fixture player ratings for the Premier League (league id 39); the last-5 recency-
   weighted form of Appendix A is now implemented exactly as specified. Finished-fixture
   ratings are cached permanently; each refresh spends a bounded, throttled request budget.
   Requires a user-supplied API key, stored locally (DC-04), entered in the UI.
2. **FPL BPS pseudo-rating (fallback)** — when no key is present or API-Football is
   unavailable, the rating term is derived from FPL's official Bonus Points System
   aggregate (BPS per 90, mapped to the rating scale). No extra requests; never dark.

*Amended requirements:* SW-02 (external rating interface now API-Football, keyed, rate-limit
compliant); FR-DATA-03 (recent form = last-5 recency-weighted per-match ratings, satisfied
exactly under source 1, approximated by season BPS/90 under source 2); FR-DATA-04
(reconciliation unchanged, applied to API-Football names; BPS fallback needs no matching);
FR-DATA-06 (graceful-degradation chain is now API-Football → BPS, surfaced in the UI weights
panel with green/amber/red source health); C-5 (retired — replaced by the API-Football
licensing/rate-limit constraint above; the dormant FotMob module remains isolated per DC-03).
The "FotMobForm" term of Appendix A reads "match-rating form" with unchanged weights and
semantics. All other requirements are unaffected.

**CR-2 (approved 2026-07-04): Dynamic scoring-source registry; ICT and odds terms.**

*Change:* Appendix A generalizes from four fixed terms to a registry of weighted components:

```
Score_i = Availability_i × Σ_k( w_k · component_k,i ) / Σ_k( w_k )
          over components k available for player i
```

Registered components (defaults): match rating 0.40 (dominant, per CR-1 chain), FPL form
0.20, fixture ease 0.12, FPL xP 0.08, **ICT index 0.10** (official FPL
Influence/Creativity/Threat, normalized against the player pool), **anytime-scorer odds
0.10** (The Odds API `player_goal_scorer_anytime` market, median implied probability across
bookmakers; applicable to MID/FWD only — for GK/DEF the term is N/A and its weight
redistributes, avoiding systematic bias against defenders). A component unavailable for a
player (source down, no key, not applicable) drops out with pro-rata weight redistribution —
the generalization of the CR-1 fallback rule. The weights UI renders one input per
registered source with per-source health (green/amber/red); provider API keys (API-Football,
The Odds API) are entered in the UI and stored locally (DC-04).

*Finding recorded:* API-Football's **free tier does not include current-season data**
(fixtures/player statistics limited to seasons 2021–2023); current-season per-match ratings
require a paid plan. Until then the BPS fallback remains the active rating source, surfaced
as amber in the UI.

*Amended requirements:* Appendix A (formula above), FR-SCORE-04 (weights are per-registered-
source), UI-05 (dynamic weight inputs + key management). All other requirements unaffected.

**CR-3 (approved 2026-07-04): Custom match-rating engine as primary rating source.**

*Change:* A proprietary per-match rating engine (specified in the repository's
`RATING_SPEC.md`, implemented in `services/custom_rating.py`) replaces external rating
providers as the primary source for the "match rating" term. Tier 1 computes, from the
official FPL `element-summary` per-match raw statistics (xG, xA, xGC, saves, penalties,
defensive contributions, cards), a positional z-score surplus over seven capped categories
with position emphasis, opponent-difficulty and minutes adjustments, calibrated to a 6.0
baseline / 0.55 SD. The **shipped signal (v1.1) is volume-adjusted**: last-5 recency-weighted
rating × recent-minutes share.

*Quality gate (new):* the engine is activated only while it passes a reproducible backtest
(`app/backtest.py`): mean Spearman rank correlation of the signal against next-gameweek FPL
points must exceed the BPS baseline. Result on 2025-26 (GW6–38, 33 gameweeks): shipped signal
**+0.178** vs BPS **+0.147** (superior in 20/33 gameweeks), pure quality rating +0.068
(diagnostic). The gate writes an approval flag; if a future retune fails the gate, the app
automatically falls back to the CR-1 chain (API-Football → BPS).

*Amended:* the rating-source chain of CR-1/CR-2 becomes custom engine → API-Football → BPS.
All interfaces (scoring blend, UI, weight registry) unchanged.

**CR-4 (approved 2026-07-08): Trim scoring registry; remove key management.**

*Change:* At product-owner request, the ICT-index, fixture-ease, and anytime-scorer-odds
terms are removed from the scoring registry, leaving **match rating (0.50 default) /
FPL form (0.30) / FPL xP (0.20)**. The API-Football and The Odds API key inputs are removed
from the UI and stored keys deleted from the local database. The `api_football.py`,
`odds_api.py`, and `fotmob.py` modules remain dormant on disk (DC-03) but are not reachable
from the UI. Fixture/opponent context (original request: "opponents of players") is retained
through (a) the custom rating engine's opponent-difficulty multiplier and (b) blank-gameweek
zeroing in the scorer (O-5); double-GW upweighting via the fixture term no longer applies.
Partially reverses CR-2. All squad/transfer/chip requirements unaffected.

**CR-5 (approved 2026-07-08): Restore fixture ease; remove FPL form.**

*Change:* The fixture-ease term (FDR-derived opponent ease, home nudge, double-GW summation
per O-5) returns to the scoring registry; the FPL form term is removed. Registry defaults:
**match rating 0.50 / fixture ease 0.30 / FPL xP 0.20**. Rationale: the custom rating engine
(CR-3) already encodes recent performance on a sounder basis than points-derived form, making
the form term largely redundant with the rating term, while next-opponent difficulty is
forward-looking information no other active term fully carries. Amends CR-4's registry;
all other requirements unaffected.

---

*End of SRS v1.5. Approved 2026-07-08; implementation authorized.*
