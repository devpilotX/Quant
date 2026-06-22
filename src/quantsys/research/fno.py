"""NSE F&O bhavcopy adapter — free, official; for S1 (BANKNIFTY PCR) + SSF universe.

From each day's free F&O bhavcopy this extracts only what the combine test needs
and discards the (large) raw file:
  * BANKNIFTY index-option open interest, summed by CE / PE  -> PCR = ΣputOI/ΣcallOI
  * BANKNIFTY near-month future close + its expiry           -> the traded series
  * the set of single-stock-futures underlyings              -> PIT SSF membership

Two formats, same cut-over as the cash bhavcopy:
  * pre-2024-07  historical/DERIVATIVES/<Y>/<MON>/fo<DDMonYYYY>bhav.csv.zip
                 INSTRUMENT∈{OPTIDX,FUTIDX,OPTSTK,FUTSTK}, OPTION_TYP, OPEN_INT, ...
  * 2024-07+     content/fo/BhavCopy_NSE_FO_0_0_0_<YYYYMMDD>_F_0000.csv.zip (UDiFF)
                 FinInstrmTp∈{IDO,IDF,STO,STF}, OptnTp, OpnIntrst, ...

Per-day aggregate cached as a one-row parquet under <cache>/fno_daily; raw zips are
NOT kept (≈GBs). Parsers are pure and unit-tested on embedded fixtures.
"""

from __future__ import annotations

import csv
import io
import time
import zipfile
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

_CDN = "https://nsearchives.nseindia.com"
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Accept": "*/*",
            "Referer": "https://www.nseindia.com/"}
_UDIFF_FROM = date(2024, 7, 8)
_MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_INDEX = "BANKNIFTY"


def _sources_for(d: date) -> list[tuple[str, str]]:
    udiff = (f"{_CDN}/content/fo/BhavCopy_NSE_FO_0_0_0_{d:%Y%m%d}_F_0000.csv.zip", "udiff")
    legacy = (f"{_CDN}/content/historical/DERIVATIVES/{d.year}/{_MON[d.month - 1].upper()}/"
              f"fo{d.day:02d}{_MON[d.month - 1].upper()}{d.year}bhav.csv.zip", "legacy")
    return [udiff, legacy] if d >= _UDIFF_FROM else [legacy, udiff]


def _f(x) -> float:
    try:
        return float(str(x).strip())
    except (ValueError, AttributeError, TypeError):
        return float("nan")


def parse_udiff(text: str, d: date) -> dict:
    put_oi = call_oi = 0.0
    fut_close, fut_exp = float("nan"), None
    ssf: set[str] = set()
    for r in csv.DictReader(text.splitlines()):
        fin = (r.get("FinInstrmTp") or "").strip()
        sym = (r.get("TckrSymb") or "").strip()
        if fin == "STF":
            ssf.add(sym)
        elif sym == _INDEX and fin == "IDO":
            oi = _f(r.get("OpnIntrst"))
            if (r.get("OptnTp") or "").strip() == "PE":
                put_oi += oi
            elif (r.get("OptnTp") or "").strip() == "CE":
                call_oi += oi
        elif sym == _INDEX and fin == "IDF":
            exp = pd.to_datetime(r.get("XpryDt"), errors="coerce")
            if exp is not None and exp.date() >= d and (fut_exp is None or exp < fut_exp):
                fut_exp, fut_close = exp, _f(r.get("ClsPric"))
    return {"date": d, "put_oi": put_oi, "call_oi": call_oi,
            "fut_close": fut_close, "fut_expiry": fut_exp, "ssf": sorted(ssf)}


def parse_legacy(text: str, d: date) -> dict:
    put_oi = call_oi = 0.0
    fut_close, fut_exp = float("nan"), None
    ssf: set[str] = set()
    for r in csv.DictReader(text.splitlines()):
        inst = (r.get("INSTRUMENT") or "").strip()
        sym = (r.get("SYMBOL") or "").strip()
        if inst == "FUTSTK":
            ssf.add(sym)
        elif sym == _INDEX and inst == "OPTIDX":
            oi = _f(r.get("OPEN_INT"))
            if (r.get("OPTION_TYP") or "").strip() == "PE":
                put_oi += oi
            elif (r.get("OPTION_TYP") or "").strip() == "CE":
                call_oi += oi
        elif sym == _INDEX and inst == "FUTIDX":
            exp = pd.to_datetime(r.get("EXPIRY_DT"), errors="coerce")
            if exp is not None and exp.date() >= d and (fut_exp is None or exp < fut_exp):
                fut_exp, fut_close = exp, _f(r.get("CLOSE"))
    return {"date": d, "put_oi": put_oi, "call_oi": call_oi,
            "fut_close": fut_close, "fut_expiry": fut_exp, "ssf": sorted(ssf)}


def _download(url: str, timeout: float, retries: int, backoff: float) -> bytes | None:
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


def _parse(raw: bytes, fmt: str, d: date) -> dict:
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        text = z.read(z.namelist()[0]).decode("utf-8", "replace")
    return parse_udiff(text, d) if fmt == "udiff" else parse_legacy(text, d)


def fetch_day(d: date, cache_dir: str | Path, *, throttle: float = 0.2,
              timeout: float = 30.0, retries: int = 3, backoff: float = 1.5) -> dict | None:
    cache_dir = Path(cache_dir)
    pdir = cache_dir / "fno_daily"
    pdir.mkdir(parents=True, exist_ok=True)
    pq = pdir / f"{d:%Y-%m-%d}.parquet"
    if pq.exists():
        row = pd.read_parquet(pq)
        if row.empty:
            return None
        rec = row.iloc[0].to_dict()
        rec["ssf"] = list(rec["ssf"]) if rec.get("ssf") is not None else []
        return rec
    rec = None
    for url, fmt in _sources_for(d):
        raw = _download(url, timeout, retries, backoff)
        time.sleep(throttle)
        if raw is None:
            continue
        rec = _parse(raw, fmt, d)
        if rec["call_oi"] > 0 or rec["ssf"]:
            break
    # cache (empty frame marks a known non-trading day)
    if rec is None:
        pd.DataFrame().to_parquet(pq)
        return None
    pd.DataFrame([{**rec, "ssf": rec["ssf"]}]).to_parquet(pq, index=False)
    return rec


def build_series(start: date, end: date, cache_dir: str | Path = "data_cache/fno",
                 progress_every: int = 200) -> pd.DataFrame:
    """Daily BANKNIFTY PCR/futures table (ssf kept as a list column)."""
    rows, d, n = [], start, 0
    while d <= end:
        if d.weekday() < 5:
            rec = fetch_day(d, cache_dir)
            if rec is not None:
                rows.append(rec)
            n += 1
            if progress_every and n % progress_every == 0:
                print(f"  fno {d:%Y-%m-%d}: {n} weekdays, {len(rows)} trading days")
        d += timedelta(days=1)
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    df["pcr"] = df["put_oi"] / df["call_oi"].replace(0.0, float("nan"))
    return df.sort_values("date").reset_index(drop=True)
