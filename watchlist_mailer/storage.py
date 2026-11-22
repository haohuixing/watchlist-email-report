"""Read and write the current snapshot under data/."""

from __future__ import annotations

from .config import DATA_DIR, SNAPSHOT_PATH, WATCHLIST_TXT_PATH
from .snapshot import Snapshot, write_watchlist_txt


def save_snapshot(snapshot: Snapshot) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    snapshot.save(SNAPSHOT_PATH)
    write_watchlist_txt(snapshot, WATCHLIST_TXT_PATH)


def load_snapshot() -> Snapshot | None:
    if not SNAPSHOT_PATH.exists():
        return None
    try:
        return Snapshot.load(SNAPSHOT_PATH)
    except (OSError, ValueError, KeyError, TypeError):
        return None
