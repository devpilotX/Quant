"""CLI: fetch point-in-time NSE OHLCV history from Angel One into the replay
format the backtester reads (one ``<SYMBOL>.csv`` per symbol with header
``ts,open,high,low,close,volume``, ts = naive IST).

Incremental + resumable: each run only fetches bars NEWER than the last row
already in a symbol's CSV, so a nightly job grows the dataset to "massive"
without re-downloading. Per-symbol failures are logged and skipped, never fatal.
Read-only against the broker (getCandleData) — it never places an order.

    python -m quantsys.data.fetch_history --out data/nse \
        --interval FIVE_MINUTE --start 2021-01-01

Then run a real backtest on it:

    python -m quantsys.backtest.runstudy --replay data/nse --persist
"""

from __future__ import annotations

import argparse
import csv
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from quantsys.config import load_config
from quantsys.core.types import Instrument, now_ist
from quantsys.execution.angelone import AngelOneBroker
from quantsys.execution.broker import BrokerError

log = logging.getLogger("quantsys.fetch_history")


def _last_ts(path: Path) -> datetime | None:
    """Timestamp of the last row already stored, for incremental resume."""
    if not path.exists():
        return None
    last = None
    with open(path, newline="") as fh:
        for row in csv.DictReader(fh):
            last = row.get("ts")
    return datetime.fromisoformat(last) if last else None


def fetch_symbol(broker: AngelOneBroker, symbol: str, interval: str,
                 start: datetime, end: datetime, out_dir: Path) -> int:
    """Fetch [resume..end] for one symbol and append to its CSV. Returns the
    number of new bars written."""
    path = out_dir / f"{symbol}.csv"
    resume = _last_ts(path)
    frm = (resume + timedelta(minutes=1)) if resume else start
    if frm > end:
        return 0
    rows = broker.historical_candles(symbol, interval, frm, end)
    if not rows:
        return 0
    is_new = not path.exists()
    with open(path, "a", newline="") as fh:
        w = csv.writer(fh)
        if is_new:
            w.writerow(["ts", "open", "high", "low", "close", "volume"])
        for ts, o, h, lo, c, v in rows:
            w.writerow([ts.isoformat(), o, h, lo, c, v])
    return len(rows)


def _ensure_tokens(broker: AngelOneBroker, cfg) -> None:
    """The instrument master is the source of truth, but a few configured
    symbols (index futures) aren't in it — fall back to their config token so
    they can still be fetched."""
    have = broker._instruments
    for u in cfg.universe:
        if (u.symbol not in have or not have[u.symbol].token) and u.token:
            have[u.symbol] = Instrument(
                symbol=u.symbol, token=u.token, exchange=u.exchange, kind=u.kind,
                lot_size=u.lot_size, tick_size=u.tick_size)


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/base.yaml")
    ap.add_argument("--out", default="data/nse")
    ap.add_argument("--interval", default="FIVE_MINUTE")
    ap.add_argument("--start", default="2021-01-01",
                    help="ISO date; only used for symbols with no CSV yet")
    ap.add_argument("--end", default=None, help="ISO datetime; default = now")
    ap.add_argument("--symbols", default=None,
                    help="comma-separated; default = the config universe")
    args = ap.parse_args()

    cfg = load_config(args.config)
    syms = (args.symbols.split(",") if args.symbols
            else [u.symbol for u in cfg.universe])
    start = datetime.fromisoformat(args.start).replace(hour=9, minute=15,
                                                       second=0, microsecond=0)
    end = (datetime.fromisoformat(args.end) if args.end else now_ist()
           ).replace(second=0, microsecond=0)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    broker = AngelOneBroker()
    broker.connect()
    _ensure_tokens(broker, cfg)

    log.info("fetching %d symbols %s [%s .. %s] -> %s",
             len(syms), args.interval, start.date(), end.date(), out_dir)
    total, ok = 0, 0
    for sym in syms:
        try:
            n = fetch_symbol(broker, sym, args.interval, start, end, out_dir)
            total += n
            ok += 1
            log.info("%-16s +%d bars", sym, n)
        except BrokerError as e:
            log.error("skip %s: %s", sym, e)
        except Exception as e:  # pragma: no cover - defensive, keep going
            log.error("skip %s (unexpected): %s", sym, e)
    log.info("done: %d/%d symbols ok, %d new bars -> %s",
             ok, len(syms), total, out_dir)


if __name__ == "__main__":
    main()
