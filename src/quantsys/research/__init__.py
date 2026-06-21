"""Research sandbox (Pillar 2 — broad-universe equity factors).

This package is INTENTIONALLY isolated from the live engine path
(`quantsys.engine`, `quantsys.portfolio`, `quantsys.risk`). Nothing here is
imported by the decision engine; it exists to fetch a broad point-in-time NSE
panel and test cross-sectional factor edges honestly (real costs, true hold-out,
deflated Sharpe + PBO + purged CV) BEFORE any production wiring.

The one shared dependency is `quantsys.costs.CostModel` and
`quantsys.backtest.metrics` — reused so research costs/metrics are identical to
the engine's, never a softer parallel implementation.
"""
