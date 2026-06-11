"""Exposure rules: each can only SHRINK the proposed book (never add risk),
each emits an audit event, and they run in a canonical order. Because every
operation is monotone-decreasing and per-symbol scalings act group-jointly,
one ordered pass cannot re-violate an earlier cap.

Order: per-instrument -> sector -> correlation clusters -> ADV -> gross ->
net -> margin.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import numpy as np

from quantsys.config.schema import ExposureConfig
from quantsys.core.types import AuditEvent, Instrument
from quantsys.portfolio.book import TargetBook
from quantsys.portfolio.tiers import TierState


@dataclass
class RiskContext:
    equity: float
    prices: Mapping[str, float]
    instruments: Mapping[str, Instrument]
    cfg: ExposureConfig
    tier: TierState
    corr_symbols: list[str]
    corr: np.ndarray | None     # correlation matrix aligned to corr_symbols


def apply_exposure_rules(book: TargetBook, ctx: RiskContext, audits: list[AuditEvent]) -> None:
    if book.empty:
        return
    _per_instrument_cap(book, ctx, audits)
    _sector_cap(book, ctx, audits)
    _corr_cluster_cap(book, ctx, audits)
    _adv_cap(book, ctx, audits)
    _global_cap(book, ctx, audits, "gross", ctx.tier.gross_leverage_cap * ctx.equity)
    _net_cap(book, ctx, audits)
    _margin_cap(book, ctx, audits)


def _scale_symbol(book: TargetBook, ctx: RiskContext, audits: list[AuditEvent],
                  rule: str, sym: str, cur: float, cap: float) -> None:
    factor = cap / cur
    book.scale_symbol(sym, factor)
    audits.append(AuditEvent("risk", rule, f"{sym}: {cur:,.0f} -> {cap:,.0f}",
                             symbol=sym, before=cur, after=cap))


def _per_instrument_cap(book: TargetBook, ctx: RiskContext, audits: list[AuditEvent]) -> None:
    cap = ctx.cfg.per_instrument_frac * ctx.equity
    for sym, nn in sorted(book.net_notional(ctx.prices, ctx.instruments).items()):
        if abs(nn) > cap > 0:
            _scale_symbol(book, ctx, audits, "per_instrument_cap", sym, abs(nn), cap)


def _sector_cap(book: TargetBook, ctx: RiskContext, audits: list[AuditEvent]) -> None:
    cap = ctx.cfg.sector_frac * ctx.equity
    nn = book.net_notional(ctx.prices, ctx.instruments)
    sectors: dict[str, list[str]] = {}
    for sym in nn:
        sec = (ctx.instruments[sym].sector or "UNKNOWN") if sym in ctx.instruments else "UNKNOWN"
        sectors.setdefault(sec, []).append(sym)
    for sec in sorted(sectors):
        gross = sum(abs(nn[s]) for s in sectors[sec])
        if gross > cap > 0:
            factor = cap / gross
            for s in sorted(sectors[sec]):
                book.scale_symbol(s, factor)
            audits.append(AuditEvent("risk", "sector_cap", f"{sec}: {gross:,.0f} -> {cap:,.0f}",
                                     before=gross, after=cap))


def _corr_cluster_cap(book: TargetBook, ctx: RiskContext, audits: list[AuditEvent]) -> None:
    if ctx.corr is None or len(ctx.corr_symbols) < 2:
        return
    cap = ctx.cfg.corr_cluster_frac * ctx.equity
    nn = book.net_notional(ctx.prices, ctx.instruments)
    idx = {s: i for i, s in enumerate(ctx.corr_symbols)}
    in_book = [s for s in ctx.corr_symbols if s in nn]
    # connected components over |rho| >= threshold
    seen: set[str] = set()
    for s in in_book:
        if s in seen:
            continue
        cluster, stack = [], [s]
        while stack:
            u = stack.pop()
            if u in seen:
                continue
            seen.add(u)
            cluster.append(u)
            for v in in_book:
                if v not in seen and abs(ctx.corr[idx[u], idx[v]]) >= ctx.cfg.corr_threshold:
                    stack.append(v)
        if len(cluster) < 2:
            continue
        gross = sum(abs(nn[c]) for c in cluster)
        if gross > cap > 0:
            factor = cap / gross
            for c in sorted(cluster):
                book.scale_symbol(c, factor)
            audits.append(AuditEvent("risk", "corr_cluster_cap",
                                     f"{sorted(cluster)}: {gross:,.0f} -> {cap:,.0f}",
                                     before=gross, after=cap))


def _adv_cap(book: TargetBook, ctx: RiskContext, audits: list[AuditEvent]) -> None:
    for sym, q in sorted(book.net_qty().items()):
        inst = ctx.instruments.get(sym)
        if inst is None or not inst.adv or inst.adv <= 0:
            continue
        cap_qty = ctx.tier.adv_cap_pct * inst.adv
        if abs(q) > cap_qty > 0:
            factor = cap_qty / abs(q)
            book.scale_symbol(sym, factor)
            audits.append(AuditEvent("risk", "adv_cap", f"{sym}: qty {q:,.0f} -> {cap_qty:,.0f}",
                                     symbol=sym, before=abs(q), after=cap_qty))


def _global_cap(book: TargetBook, ctx: RiskContext, audits: list[AuditEvent],
                name: str, cap: float) -> None:
    gross = book.gross(ctx.prices, ctx.instruments)
    if gross > cap > 0:
        book.scale_all(cap / gross)
        audits.append(AuditEvent("risk", f"{name}_cap", f"{gross:,.0f} -> {cap:,.0f}",
                                 before=gross, after=cap))


def _net_cap(book: TargetBook, ctx: RiskContext, audits: list[AuditEvent]) -> None:
    cap = ctx.cfg.net_frac * ctx.equity
    net = abs(book.net(ctx.prices, ctx.instruments))
    if net > cap > 0:
        book.scale_all(cap / net)
        audits.append(AuditEvent("risk", "net_cap", f"{net:,.0f} -> {cap:,.0f}",
                                 before=net, after=cap))


def _margin_cap(book: TargetBook, ctx: RiskContext, audits: list[AuditEvent]) -> None:
    cap = ctx.cfg.margin_util_cap * ctx.equity
    nn = book.net_notional(ctx.prices, ctx.instruments)
    margin = sum(abs(v) * ctx.instruments[s].margin_rate for s, v in nn.items() if s in ctx.instruments)
    if margin > cap > 0:
        book.scale_all(cap / margin)
        audits.append(AuditEvent("risk", "margin_cap", f"{margin:,.0f} -> {cap:,.0f}",
                                 before=margin, after=cap))
