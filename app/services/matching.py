"""FotMob -> FPL player reconciliation (FR-DATA-04)."""
from rapidfuzz import fuzz, process

MATCH_THRESHOLD = 82  # fuzz score below this -> treat as unmatched


def _norm(s: str) -> str:
    return s.lower().replace("-", " ").replace(".", "").strip()


def attach_field(players: list[dict], rows: list[dict] | None,
                 teams: dict[int, dict], value_key: str, target_field: str) -> int:
    """Fuzzy-match external rows onto players and set p[target_field] from
    row[value_key]. Rows: [{name, team, <value_key>}]. Returns match count."""
    for p in players:
        p[target_field] = None
    if not rows:
        return 0

    names = [_norm(r["name"]) for r in rows]
    matched = 0
    for p in players:
        team_name = teams[p["team"]]["name"]
        full = _norm(f'{p["first_name"]} {p["second_name"]}')
        web = _norm(p["web_name"])

        best = process.extractOne(full, names, scorer=fuzz.token_sort_ratio)
        alt = process.extractOne(web, names, scorer=fuzz.partial_ratio)
        candidate = None
        if best and best[1] >= MATCH_THRESHOLD:
            candidate = (best[2], best[1])
        elif alt and alt[1] >= 90:  # web_name is short; require stronger score
            candidate = (alt[2], alt[1])

        if candidate is None:
            continue
        row = rows[candidate[0]]
        # Disambiguate common names by club when the source provides one.
        if row.get("team") and fuzz.partial_ratio(_norm(row["team"]), _norm(team_name)) < 60:
            continue
        p[target_field] = row[value_key]
        matched += 1
    return matched


def attach_fotmob_ratings(players: list[dict], rows: list[dict] | None,
                          teams: dict[int, dict]) -> int:
    """Back-compat wrapper: match-rating rows -> p['fotmob_rating']."""
    return attach_field(players, rows, teams, "rating", "fotmob_rating")
