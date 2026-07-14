"""
app.py
======
FastAPI application entry point for the Pendulastic clinical interface.

Startup:
    uvicorn web.api.app:app --reload --port 8000

All endpoints are prefixed under /api.  CORS is opened to the Vite dev
server (localhost:5173) by default; set FRONTEND_ORIGIN in the environment
to override for production.
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from web.api.routers import participants, trials, ws_stream
from web.api.store import trial_db  # shared in-memory store

# Web frontend dev server origin (Vite).  The Expo mobile client connects
# directly to the WebSocket; no CORS restriction applies to WS connections.
FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", "http://localhost:5173")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler — no external connections required at startup."""
    yield


app = FastAPI(
    title="Pendulastic Clinical API",
    version="2.1.0",
    description="REST interface for the Pendulastic knee spasticity evaluation platform.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(trials.router,       prefix="/api/trials",       tags=["trials"])
app.include_router(participants.router, prefix="/api/participants", tags=["participants"])
# WebSocket router is mounted without /api prefix — WS clients connect to
# ws://<host>:8000/ws/stream directly.
app.include_router(ws_stream.router, tags=["stream"])


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok", "version": app.version}
