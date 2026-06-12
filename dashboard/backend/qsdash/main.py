"""FastAPI application. Run: uvicorn qsdash.main:app --port 8000"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from qsdash import __version__
from qsdash.api import auth, control, data, dbadmin, webhooks
from qsdash.config import settings
from qsdash.db import now_ist
from qsdash.ws import hub
from qsdash.ws import router as ws_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await hub.start()
    yield
    await hub.stop()


app = FastAPI(title="quantsys control plane", version=__version__, lifespan=lifespan,
              docs_url="/api/docs" if settings.env == "dev" else None,
              openapi_url="/api/openapi.json" if settings.env == "dev" else None)

_origins = [settings.public_origin]
if settings.env == "dev":
    _origins += ["http://localhost:3000", "http://127.0.0.1:3000"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/api")
app.include_router(control.router, prefix="/api")
app.include_router(data.router, prefix="/api")
app.include_router(dbadmin.router, prefix="/api")
app.include_router(webhooks.router, prefix="/api")
app.include_router(ws_router)


@app.get("/api/health")
def health():
    return {"ok": True, "ts": now_ist().isoformat(), "version": __version__}
