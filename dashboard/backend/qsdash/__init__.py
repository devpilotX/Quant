"""qsdash — live dashboard & control plane for the quantsys trading engine.

Architecture contract (see docs/ARCHITECTURE.md):
- The ENGINE process is the only writer of trading truth (decisions, orders,
  fills, positions, equity). It persists to Postgres and publishes events.
- The API process reads Postgres and relays events to browsers over WebSocket.
  Control actions never mutate engine state directly: they enqueue a row in
  ``commands`` (+ ``config_versions``) which the engine consumes and acks.
- A dashboard crash therefore cannot affect trading, and vice-versa.
"""

__version__ = "0.1.0"
