"""Synthetic bar generation: correlated GBM with a slow volatility-regime
cycle, session-aware NSE timestamps. Used by the backtester's mechanical
validation and by the dashboard's dev runner (single implementation).

Synthetic data validates the MACHINE (accounting, no-lookahead, costs,
determinism) — it is NOT evidence of edge. Real conclusions require real
point-in-time NSE history via the execution layer's historical fetcher.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Iterator

import numpy as np

from quantsys.core.types import Bar, is_session_open


def synthetic_bars(
    symbols: list[str],
    start: datetime,
    n_bars: int,
    bar_minutes: int,
    seed: int = 7,
) -> Iterator[tuple[datetime, dict[str, Bar]]]:
    rng = np.random.default_rng(seed)
    k = len(symbols)
    base = rng.uniform(0.2, 1.0, size=(k, k))
    corr = 0.4 * (base @ base.T)
    d = np.sqrt(np.diag(corr))
    corr = corr / np.outer(d, d)
    np.fill_diagonal(corr, 1.0)
    chol = np.linalg.cholesky(corr + 1e-9 * np.eye(k))
    prices = rng.uniform(80, 3000, size=k)
    ann_vol = rng.uniform(0.12, 0.35, size=k)
    bar_vol = ann_vol / math.sqrt(252 * 375 / bar_minutes)
    drift = rng.normal(0.02, 0.06, size=k) / (252 * 375 / bar_minutes)

    ts = start.replace(hour=9, minute=15, second=0, microsecond=0)
    for i in range(n_bars):
        if not is_session_open(ts):
            ts = (ts + timedelta(days=1)).replace(hour=9, minute=15)
            while ts.weekday() >= 5:
                ts += timedelta(days=1)
        regime = 1.0 + 1.4 * (0.5 + 0.5 * math.sin(2 * math.pi * i / 2200)) ** 3
        z = chol @ rng.standard_normal(k)
        rets = drift + bar_vol * regime * z
        new_prices = prices * np.exp(rets)
        out: dict[str, Bar] = {}
        for j, sym in enumerate(symbols):
            o, c = prices[j], new_prices[j]
            hi = max(o, c) * (1 + abs(rng.normal(0, 0.0006)))
            lo = min(o, c) * (1 - abs(rng.normal(0, 0.0006)))
            vol = float(rng.lognormal(11, 0.6))
            out[sym] = Bar(ts=ts, open=float(o), high=float(hi),
                           low=float(lo), close=float(c), volume=vol)
        prices = new_prices
        yield ts, out
        ts += timedelta(minutes=bar_minutes)


def replay_bars(directory: str, symbols: list[str]
                ) -> Iterator[tuple[datetime, dict[str, Bar]]]:
    """CSV replay: <SYMBOL>.csv with header ts,open,high,low,close,volume.
    Bars are merged on their timestamps; symbols missing a bar at a given ts
    simply have no entry that step (point-in-time faithful)."""
    import csv
    from pathlib import Path

    streams: dict[str, list[Bar]] = {}
    for sym in symbols:
        f = Path(directory) / f"{sym}.csv"
        if not f.exists():
            continue
        rows: list[Bar] = []
        with open(f, newline="") as fh:
            for r in csv.DictReader(fh):
                rows.append(Bar(
                    ts=datetime.fromisoformat(r["ts"]), open=float(r["open"]),
                    high=float(r["high"]), low=float(r["low"]),
                    close=float(r["close"]), volume=float(r.get("volume", 0) or 0),
                ))
        rows.sort(key=lambda b: b.ts)
        streams[sym] = rows
    all_ts = sorted({b.ts for rows in streams.values() for b in rows})
    idx = {sym: 0 for sym in streams}
    for ts in all_ts:
        out: dict[str, Bar] = {}
        for sym, rows in streams.items():
            i = idx[sym]
            if i < len(rows) and rows[i].ts == ts:
                out[sym] = rows[i]
                idx[sym] = i + 1
        if out:
            yield ts, out
