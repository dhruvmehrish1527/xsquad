"""Seed state for ephemeral-disk deployments (e.g. Hugging Face Spaces).

Free hosts wipe the filesystem on restart. To keep the app fully functional
without re-running the multi-minute rating fetch server-side, the engine's
published ratings, backtest gate flag, and point calibration are exported to
seed/state.json (committed) and loaded into a fresh database at startup.

Export from a machine with a populated DB:
    .venv/bin/python -m app.seed --export
"""
import json
from pathlib import Path

from . import db

SEED_PATH = Path(__file__).resolve().parent.parent / "seed" / "state.json"

KV_KEYS = ["custom_approved", "points_calibration", "weights"]
CACHE_KEYS = ["custom:ratings"]


def load_if_needed() -> bool:
    """Fill in any seed keys the DB doesn't have yet. Per-key, so a value
    already computed in this environment (e.g. a backtest-gate verdict) is
    never overwritten by the seed. Returns True if anything was loaded."""
    if not SEED_PATH.exists():
        return False
    data = json.loads(SEED_PATH.read_text())
    loaded = False
    for k, v in data.get("kv", {}).items():
        if v is not None and db.kv_get(k) is None:
            db.kv_set(k, v)
            loaded = True
    for k, v in data.get("cache", {}).items():
        if v is not None and db.cache_get(k) is None:
            db.cache_put(k, v)
            loaded = True
    return loaded


def export() -> None:
    data = {
        "kv": {k: db.kv_get(k) for k in KV_KEYS},
        "cache": {k: db.cache_get(k) for k in CACHE_KEYS},
    }
    SEED_PATH.parent.mkdir(parents=True, exist_ok=True)
    SEED_PATH.write_text(json.dumps(data))
    n = len((data["cache"].get("custom:ratings") or {}).get("ratings", {}))
    print(f"Exported seed to {SEED_PATH} ({n} player ratings, "
          f"gate={data['kv']['custom_approved']})")


if __name__ == "__main__":
    import sys
    if "--export" in sys.argv:
        export()
    else:
        print("seeded" if load_if_needed() else "no seeding needed")
