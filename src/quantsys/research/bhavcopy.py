"""NSE cash-market bhavcopy adapter — free, official, point-in-time.

Builds a survivorship-bias-free daily equity panel directly from NSE's public
archive CDN (``nsearchives.nseindia.com``, which — unlike ``www.nseindia.com`` —
serves these files without bot-protection). A symbol appears on a given day iff
it actually traded that day, so the universe is point-in-time by construction:
no look-ahead, no survivorship bias (delisted names are present in their era and
simply stop appearing).

Two on-disk formats are handled transparently:
  * pre-2024-07  ``content/historical/EQUITIES/<Y>/<MON>/cm<DDMONYYYY>bhav.csv.zip``
                 cols: SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,...,TOTTRDQTY,TOTTRDVAL,TIMESTAMP,...,ISIN
  * 2024-07+     ``content/cm/BhavCopy_NSE_CM_0_0_0_<YYYYMMDD>_F_0000.csv.zip`` (UDiFF)
                 cols: TradDt,...,TckrSymb,SctySrs,...,OpnPric,...,ClsPric,...,TtlTradgVol,TtlTrfVal,...

Only the EQ series is kept (the liquid rolling-settlement segment). Everything is
cached: raw downloads under ``<cache>/raw`` and the normalised per-day parquet
under ``<cache>/parsed``, so a re-run is offline and instant. Non-trading days
return 404 and are recorded as empty so they are never re-fetched.

Network access is confined to ``_download``; ``parse_*`` are pure and unit-tested
on embedded fixtures (no network in the test suite).
"""

from __future__ import annotations

import csv
import io
import time
import zipfile
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

_CDN = "https://nsearchives.nseindia.com"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept": "*/*",
    "Referer": "https://www.nseindia.com/",
}
# UDiFF replaced the legacy cm<...>bhav file from ~2024-07-08.
_UDIFF_FROM = date(2024, 7, 8)
_MON = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]

# Normalised tidy schema produced by every parser. `prevclose` is NSE's
# corporate-action-ADJUSTED previous close, so close/prevclose-1 is the
# split/bonus/dividend-adjusted daily return — the basis for all factor returns.
COLUMNS = ["date", "symbol", "open", "high", "low", "close", "prevclose",
           "volume", "turnover", "isin"]


@dataclass(frozen=True)
class _Source:
    url: str
    fmt: str  # "udiff" | "legacy"


def _sources_for(d: date) -> list[_Source]:
    """Primary source for the date, plus the other format as a fallback (the
    cut-over week is fuzzy; trying both is cheap and robust)."""
    udiff = _Source(
        f"{_CDN}/content/cm/BhavCopy_NSE_CM_0_0_0_{d:%Y%m%d}_F_0000.csv.zip", "udiff"
    )
    legacy = _Source(
        f"{_CDN}/content/historical/EQUITIES/{d.year}/{_MON[d.month - 1]}/"
        f"cm{d.day:02d}{_MON[d.month - 1]}{d.year}bhav.csv.zip",
        "legacy",
    )
    return [udiff, legacy] if d >= _UDIFF_FROM else [legacy, udiff]


# --------------------------------------------------------------------- parsers
def _f(x: str) -> float:
    try:
        return float(str(x).strip())
    except (ValueError, AttributeError):
        return float("nan")


def parse_udiff(text: str, d: date) -> list[dict]:
    out: list[dict] = []
    for r in csv.DictReader(text.splitlines()):
        if (r.get("SctySrs") or "").strip() != "EQ":
            continue
        fin = (r.get("FinInstrmTp") or "").strip()
        if fin and fin != "STK":          # keep cash stocks only; drop IDX/ETF/etc.
            continue
        out.append({
            "date": d,
            "symbol": (r.get("TckrSymb") or "").strip(),
            "open": _f(r.get("OpnPric")),
            "high": _f(r.get("HghPric")),
            "low": _f(r.get("LwPric")),
            "close": _f(r.get("ClsPric")),
            "prevclose": _f(r.get("PrvsClsgPric")),
            "volume": _f(r.get("TtlTradgVol")),
            "turnover": _f(r.get("TtlTrfVal")),
            "isin": (r.get("ISIN") or "").strip(),
        })
    return out


def parse_legacy(text: str, d: date) -> list[dict]:
    out: list[dict] = []
    for r in csv.DictReader(text.splitlines()):
        if (r.get("SERIES") or "").strip() != "EQ":
            continue
        out.append({
            "date": d,
            "symbol": (r.get("SYMBOL") or "").strip(),
            "open": _f(r.get("OPEN")),
            "high": _f(r.get("HIGH")),
            "low": _f(r.get("LOW")),
            "close": _f(r.get("CLOSE")),
            "prevclose": _f(r.get("PREVCLOSE")),
            "volume": _f(r.get("TOTTRDQTY")),
            "turnover": _f(r.get("TOTTRDVAL")),
            "isin": (r.get("ISIN") or "").strip(),
        })
    return out


def _parse(raw: bytes, fmt: str, d: date) -> list[dict]:
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        text = z.read(z.namelist()[0]).decode("utf-8", "replace")
    return parse_udiff(text, d) if fmt == "udiff" else parse_legacy(text, d)


# ------------------------------------------------------------------- download
def _download(url: str, timeout: float, retries: int, backoff: float) -> bytes | None:
    """Return zip bytes, None on 404 (non-trading day / missing). Retries on
    transient errors with exponential backoff. Imported lazily so the parsers
    stay importable in a no-network test environment."""
    import requests

    for attempt in range(retries + 1):
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=timeout)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.content
        except Exception:
            if attempt == retries:
                raise
            time.sleep(backoff * (2 ** attempt))
    return None


def fetch_day(
    d: date,
    cache_dir: str | Path,
    *,
    throttle: float = 0.25,
    timeout: float = 20.0,
    retries: int = 3,
    backoff: float = 1.5,
) -> pd.DataFrame:
    """One trading day → normalised DataFrame (possibly empty on holidays).

    Cached as ``<cache>/parsed/<YYYY-MM-DD>.parquet``; an empty file marks a known
    non-trading day so it is never re-requested. Raw zips are kept under
    ``<cache>/raw`` for auditability."""
    cache_dir = Path(cache_dir)
    parsed_dir = cache_dir / "parsed"
    parsed_dir.mkdir(parents=True, exist_ok=True)
    pq = parsed_dir / f"{d:%Y-%m-%d}.parquet"
    if pq.exists():
        return pd.read_parquet(pq)

    raw_dir = cache_dir / "raw"
    rows: list[dict] = []
    for src in _sources_for(d):
        cached = raw_dir / f"{d:%Y-%m-%d}_{src.fmt}.zip"
        if cached.exists():                       # re-parse offline (no network)
            rows = _parse(cached.read_bytes(), src.fmt, d)
            if rows:
                break
            continue
        raw = _download(src.url, timeout, retries, backoff)
        time.sleep(throttle)
        if raw is None:
            continue
        raw_dir.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(raw)
        rows = _parse(raw, src.fmt, d)
        if rows:
            break

    df = pd.DataFrame(rows, columns=COLUMNS)
    df.to_parquet(pq, index=False)  # empty frame == cached "no trading" marker
    return df


def build_panel(
    start: date,
    end: date,
    cache_dir: str | Path = "data_cache/bhavcopy",
    *,
    progress_every: int = 100,
    **fetch_kw,
) -> pd.DataFrame:
    """Tidy long panel for [start, end] inclusive, fetching+caching per day.

    Skips weekends a priori; holidays are discovered (empty) and cached. Safe to
    interrupt and resume — completed days are read from cache."""
    cache_dir = Path(cache_dir)
    frames: list[pd.DataFrame] = []
    d, n, got = start, 0, 0
    while d <= end:
        if d.weekday() < 5:  # Mon–Fri
            df = fetch_day(d, cache_dir, **fetch_kw)
            if len(df):
                frames.append(df)
                got += 1
            n += 1
            if progress_every and n % progress_every == 0:
                print(f"  bhavcopy {d:%Y-%m-%d}: {n} weekdays scanned, {got} trading days")
        d += timedelta(days=1)
    if not frames:
        return pd.DataFrame(columns=COLUMNS)
    panel = pd.concat(frames, ignore_index=True)
    panel["date"] = pd.to_datetime(panel["date"])
    return panel.sort_values(["date", "symbol"]).reset_index(drop=True)


def close_panel(tidy: pd.DataFrame, field: str = "close") -> pd.DataFrame:
    """Pivot a tidy panel into a (date × symbol) wide matrix of `field`."""
    wide = tidy.pivot_table(index="date", columns="symbol", values=field, aggfunc="last")
    return wide.sort_index()
