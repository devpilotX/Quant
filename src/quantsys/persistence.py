"""Atomic JSON snapshots of engine state so a restart resumes mid-session
without losing kill-switch status, high-water mark, stops, edge statistics,
or strategy episodes."""

from __future__ import annotations

import json
import os
from pathlib import Path


def save_state(path: str | Path, state: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=1, default=str)
    os.replace(tmp, path)


def load_state(path: str | Path) -> dict | None:
    path = Path(path)
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)
