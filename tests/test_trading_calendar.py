"""NSE trading-calendar guard.

Regression cover for the Study-2 study-integrity defect found 2026-07-26:
``is_session_open`` tested the wall clock ONLY, with no weekday and no holiday
check, so 09:15-15:30 on a Saturday, a Sunday or an exchange holiday counted as
an open session. Consequences measured in the paper DB over 2026-07-02..25:

- 32.2% of ALL recorded bars (10,497 / 32,561) were zero-volume flat
  fabrications — the broker replaying its last snapshot into a shut market;
- the engine decided and filled orders on 4 Saturdays and 3 Sundays (769 fills)
  plus the Muharram holiday (Fri 2026-06-26: 350/350 synthetic bars, 11 fills),
  paying ~Rs 1.03 lakh of modelled fees on days NSE never opened;
- the feed never PARKED outside the session (its activity window is derived from
  the same predicate), so it reconnect-churned all weekend: 1,820 "feed stale"
  alerts and 274 engine restarts.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from quantsys.core.types import NSE_HOLIDAYS, is_session_open, is_trading_day
from quantsys.execution.marketdata import BarAggregator

# 2026-07-24 Fri / 07-25 Sat / 07-26 Sun — the exact weekend the defect was found on.
FRI = date(2026, 7, 24)
SAT = date(2026, 7, 25)
SUN = date(2026, 7, 26)
MUHARRAM = date(2026, 6, 26)      # Friday holiday: validated 100% synthetic in our own feed
REPUBLIC_DAY = date(2026, 1, 26)  # Monday holiday


def _at(d: date, hh: int, mm: int) -> datetime:
    return datetime(d.year, d.month, d.day, hh, mm)


# ------------------------------------------------------------ trading days
def test_weekends_are_not_trading_days():
    assert is_trading_day(FRI)
    assert not is_trading_day(SAT)
    assert not is_trading_day(SUN)


def test_exchange_holidays_are_not_trading_days():
    assert not is_trading_day(MUHARRAM)
    assert not is_trading_day(REPUBLIC_DAY)
    # every published closure must be a weekday: a weekend entry would mean the
    # list was transcribed wrong (weekend dates are covered by the weekday test)
    for d in NSE_HOLIDAYS:
        assert d.weekday() < 5, f"{d} is a weekend; drop it from NSE_HOLIDAYS"


def test_is_trading_day_accepts_date_or_datetime():
    assert is_trading_day(_at(FRI, 12, 0)) is True
    assert is_trading_day(_at(SAT, 12, 0)) is False


# ------------------------------------------------------------ session hours
def test_session_open_only_inside_trading_hours_on_trading_days():
    assert not is_session_open(_at(FRI, 9, 14))    # pre-open
    assert is_session_open(_at(FRI, 9, 15))        # open bell
    assert is_session_open(_at(FRI, 15, 29))       # last minute
    assert not is_session_open(_at(FRI, 15, 30))   # close is exclusive
    assert not is_session_open(_at(FRI, 18, 30))   # evening snapshot burst


def test_session_never_open_on_non_trading_days():
    for d in (SAT, SUN, MUHARRAM, REPUBLIC_DAY):
        for hh, mm in ((9, 15), (12, 0), (15, 29)):
            assert not is_session_open(_at(d, hh, mm)), f"{d} {hh}:{mm} must be shut"


# -------------------------------------------------- aggregator tick gating
def test_aggregator_ignores_weekend_and_holiday_ticks():
    """The broker replays its last snapshot when the market is shut; those ticks
    must never become bars (they were flat + zero-volume, and the engine traded
    on them)."""
    emitted: list = []
    agg = BarAggregator(15, lambda s, b: emitted.append((s, b)))
    for d in (SAT, SUN, MUHARRAM):
        agg.on_tick("RELIANCE", 1326.5, _at(d, 9, 15), cum_volume=0.0)
        agg.on_tick("RELIANCE", 1326.5, _at(d, 12, 0), cum_volume=0.0)
    agg.flush()
    assert emitted == []


def test_aggregator_ignores_post_close_ticks():
    emitted: list = []
    agg = BarAggregator(15, lambda s, b: emitted.append((s, b)))
    agg.on_tick("RELIANCE", 1329.0, _at(FRI, 15, 30))
    agg.on_tick("RELIANCE", 1326.5, _at(FRI, 15, 45))
    agg.on_tick("RELIANCE", 1326.5, _at(FRI, 16, 0))
    agg.on_tick("RELIANCE", 1326.5, _at(FRI, 18, 30))
    agg.flush()
    assert emitted == []


def test_aggregator_still_builds_normal_session_bars():
    """The guard must not cost us any real bar, including the session-close one."""
    emitted: list = []
    agg = BarAggregator(15, lambda s, b: emitted.append(b))
    base = _at(FRI, 9, 15)
    agg.on_tick("RELIANCE", 1293.0, base, cum_volume=0.0)
    agg.on_tick("RELIANCE", 1311.55, base + timedelta(minutes=5), cum_volume=56890.0)
    agg.on_tick("RELIANCE", 1311.2, base + timedelta(minutes=14), cum_volume=56890.0)
    assert emitted == []                                   # 09:15 bucket still open
    assert agg.flush_older(_at(FRI, 9, 30)) == 1           # window elapsed
    bar = emitted[0]
    assert bar.ts == base
    assert (bar.open, bar.high, bar.low, bar.close) == (1293.0, 1311.55, 1293.0, 1311.2)
    assert bar.volume == 56890.0
    # the last bar of the session (15:15-15:29) must still complete
    agg.on_tick("RELIANCE", 1324.75, _at(FRI, 15, 16), cum_volume=56890.0)
    agg.on_tick("RELIANCE", 1329.0, _at(FRI, 15, 29), cum_volume=121739.0)
    assert agg.flush_older(_at(FRI, 15, 31)) == 1
    assert emitted[-1].ts == _at(FRI, 15, 15)
