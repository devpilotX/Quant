"""Timescale hypertables where the extension exists (VPS); no-op otherwise.

market_bars and equity_curve are the high-cardinality time series. Converting
is conditional so the same migration chain runs on vanilla Postgres (local
dev) and TimescaleDB (production).
"""
from typing import Sequence, Union

from alembic import op

revision: str = "c1d2e3f40001"
down_revision: Union[str, None] = "b3ea7b500e54"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_UP = """
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'timescaledb') THEN
        CREATE EXTENSION IF NOT EXISTS timescaledb;
        -- hypertables need the partition column in any unique index;
        -- market_bars PK already includes ts. equity_curve uses a plain id PK,
        -- so it stays a normal table unless re-keyed — convert only market_bars.
        IF NOT EXISTS (
            SELECT 1 FROM timescaledb_information.hypertables
            WHERE hypertable_name = 'market_bars'
        ) THEN
            PERFORM create_hypertable('market_bars', 'ts',
                                      migrate_data => true,
                                      if_not_exists => true);
        END IF;
    END IF;
END
$$;
"""


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(_UP)


def downgrade() -> None:
    pass  # converting back from a hypertable is a manual operation
