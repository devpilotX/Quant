"""Read-only database browser under the same auth.

Full pgweb runs as a separate container behind nginx (/db/) on the VPS; this
endpoint set is the always-available, zero-extra-process fallback and the one
the SPA's Database page uses. Strictly read-only:

- table list restricted to an allowlist (every qsdash table),
- ad-hoc queries must be a single SELECT (no semicolons, no CTE-wrapped DML),
  run on a session pinned to ``default_transaction_read_only = on``.
"""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from qsdash.db import Base, engine
from qsdash.deps import current_session, get_db, require_csrf

router = APIRouter(prefix="/db", tags=["dbadmin"], dependencies=[Depends(current_session)])

ALLOWED_TABLES = sorted(Base.metadata.tables.keys())

_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|grant|revoke|truncate|vacuum|copy|do|call|set|listen|notify)\b",
    re.IGNORECASE,
)


@router.get("/tables")
def tables():
    insp = inspect(engine)
    out = []
    for t in ALLOWED_TABLES:
        try:
            cols = [c["name"] for c in insp.get_columns(t)]
        except Exception:
            cols = []
        out.append({"table": t, "columns": cols})
    return out


@router.get("/tables/{table}")
def table_rows(table: str, limit: int = 100, offset: int = 0,
               order: str = "desc", db: Session = Depends(get_db)):
    if table not in ALLOWED_TABLES:
        raise HTTPException(404, "unknown table")
    limit = min(limit, 500)
    tbl = Base.metadata.tables[table]
    pk = list(tbl.primary_key.columns)
    order_clause = ""
    if pk:
        direction = "DESC" if order == "desc" else "ASC"
        order_clause = f' ORDER BY "{pk[0].name}" {direction}'
    rows = db.execute(
        text(f'SELECT * FROM "{table}"{order_clause} LIMIT :lim OFFSET :off'),
        {"lim": limit, "off": offset},
    )
    cols = list(rows.keys())
    total = db.execute(text(f'SELECT count(*) FROM "{table}"')).scalar()
    return {
        "table": table, "columns": cols, "total": total,
        "rows": [[_jsonable(v) for v in r] for r in rows.fetchall()],
    }


class QueryBody(BaseModel):
    sql: str
    limit: int = 200


@router.post("/query")
def run_query(body: QueryBody, db: Session = Depends(get_db),
              _sess=Depends(require_csrf)):
    sql = body.sql.strip().rstrip(";")
    if ";" in sql:
        raise HTTPException(400, "single statement only")
    if not re.match(r"^\s*(select|with|explain)\b", sql, re.IGNORECASE):
        raise HTTPException(400, "SELECT/EXPLAIN only")
    if _FORBIDDEN.search(sql):
        raise HTTPException(400, "read-only: statement contains a write keyword")
    db.execute(text("SET TRANSACTION READ ONLY"))
    try:
        rows = db.execute(text(f"SELECT * FROM ({sql}) _q LIMIT :lim")
                          if not sql.lower().startswith("explain")
                          else text(sql), {"lim": min(body.limit, 1000)})
        cols = list(rows.keys())
        data = [[_jsonable(v) for v in r] for r in rows.fetchall()]
    except Exception as e:
        raise HTTPException(400, f"query error: {e}")
    finally:
        db.rollback()  # never leave a transaction open; nothing to commit
    return {"columns": cols, "rows": data}


def _jsonable(v):
    from datetime import date, datetime

    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, (dict, list, str, int, float, bool)) or v is None:
        return v
    return str(v)
