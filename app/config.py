"""Application configuration — scoring-source registry and tunables (FR-CFG-01).

Scoring weights are a dynamic registry (CR-2): each source has a key, UI
label, default weight, and a provider whose health drives the red/amber/green
indicator on its weight input. New sources plug in here + a component in
scoring.py without further UI changes.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class SourceDef:
    key: str        # weight key + scoring component name
    label: str      # UI label
    default: float  # default weight (rating stays dominant per SRS Appendix A)
    provider: str   # 'rating' | 'fpl' | 'odds' — health indicator group


# CR-4 trimmed the registry; CR-5 swapped FPL form out for fixture ease.
SOURCES: list[SourceDef] = [
    SourceDef("rating",  "Match rating", 0.50, "rating"),
    SourceDef("fixture", "Fixture ease", 0.30, "fpl"),
    SourceDef("xp",      "FPL xP",       0.20, "fpl"),
]

DEFAULT_WEIGHTS: dict[str, float] = {s.key: s.default for s in SOURCES}


@dataclass
class Settings:
    budget_tenths: int = 1000            # C-1: £100.0m in integer tenths (DC-02)
    max_per_club: int = 3                # C-3
    squad_gk: int = 2                    # C-2
    squad_def: int = 5
    squad_mid: int = 5
    squad_fwd: int = 3
    fotmob_window: int = 5               # O-2: last N matches, recency-weighted
    freshness_seconds: int = 3600        # FR-DATA-05 cache window
    fotmob_enabled: bool = False         # retired per CR-1; module kept dormant
    fotmob_timeout: float = 8.0
    fotmob_throttle_seconds: float = 1.0 # COM-02 conservative throttle
    user_agent: str = "fpl-optimizer/1.0 (personal, single-user)"
    solver_time_limit: int = 30          # PERF-01 guard


# Legal FPL formations (C-4): 1 GK fixed; (DEF, MID, FWD) summing to 10.
LEGAL_FORMATIONS: list[tuple[int, int, int]] = [
    (d, m, f)
    for d in range(3, 6)
    for m in range(2, 6)
    for f in range(1, 4)
    if d + m + f == 10
]

settings = Settings()
