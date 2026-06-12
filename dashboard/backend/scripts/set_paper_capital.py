"""Dev seeding helper: set paper capital directly (operator path is the
control API; this is for bootstrapping a dev session)."""

import sys

from sqlalchemy import text

from qsdash.db import SessionLocal

capital = float(sys.argv[1]) if len(sys.argv) > 1 else 50_000_000.0
s = SessionLocal()
s.execute(text("UPDATE runtime_config SET value = :v WHERE key = 'paper_capital'"),
          {"v": f'{{"v": {capital}}}'})
s.commit()
s.close()
print(f"paper_capital -> {capital:,.0f}")
