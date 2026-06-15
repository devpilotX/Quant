"""Historical-data fetcher: CSV output is incremental/resumable and round-trips
through the backtester's replay loader (so a fetch directly feeds runstudy)."""

from __future__ import annotations

import csv
from datetime import datetime, timedelta

from quantsys.backtest.synth import replay_bars
from quantsys.data.fetch_history import _last_ts, fetch_symbol

# five 5-min bars on one session day
_DATA = [
    (datetime(2021, 1, 4, 9, 15) + timedelta(minutes=5 * i),
     100.0 + i, 101.0 + i, 99.0 + i, 100.5 + i, 1000 + i)
    for i in range(5)
]


class _FakeBroker:
    """Returns the slice of _DATA inside the requested window."""

    def __init__(self):
        self.calls = []

    def historical_candles(self, symbol, interval, frm, to):
        self.calls.append((frm, to))
        return [r for r in _DATA if frm <= r[0] <= to]


def test_fetch_symbol_writes_replay_format(tmp_path):
    fb = _FakeBroker()
    n = fetch_symbol(fb, "SBIN-EQ", "FIVE_MINUTE",
                     datetime(2021, 1, 1, 9, 15), datetime(2021, 1, 4, 15, 30),
                     tmp_path)
    assert n == 5
    rows = list(csv.DictReader(open(tmp_path / "SBIN-EQ.csv")))
    assert [*rows[0]] == ["ts", "open", "high", "low", "close", "volume"]
    assert rows[0]["ts"] == _DATA[0][0].isoformat()
    assert _last_ts(tmp_path / "SBIN-EQ.csv") == _DATA[-1][0]


def test_fetch_symbol_is_incremental(tmp_path):
    fb = _FakeBroker()
    rng = (datetime(2021, 1, 1, 9, 15), datetime(2021, 1, 4, 15, 30))
    assert fetch_symbol(fb, "SBIN-EQ", "FIVE_MINUTE", *rng, tmp_path) == 5
    # re-run resumes past the last stored bar => nothing new, no duplicates
    assert fetch_symbol(fb, "SBIN-EQ", "FIVE_MINUTE", *rng, tmp_path) == 0
    rows = list(csv.DictReader(open(tmp_path / "SBIN-EQ.csv")))
    assert len(rows) == 5
    # the second call asked only for bars AFTER the last stored ts
    assert fb.calls[1][0] == _DATA[-1][0] + timedelta(minutes=1)


def test_fetched_csv_round_trips_into_backtester(tmp_path):
    fetch_symbol(_FakeBroker(), "SBIN-EQ", "FIVE_MINUTE",
                 datetime(2021, 1, 1, 9, 15), datetime(2021, 1, 4, 15, 30),
                 tmp_path)
    out = list(replay_bars(str(tmp_path), ["SBIN-EQ"]))
    assert len(out) == 5
    ts, bars = out[0]
    assert ts == _DATA[0][0]
    assert "SBIN-EQ" in bars and bars["SBIN-EQ"].close == _DATA[0][4]
