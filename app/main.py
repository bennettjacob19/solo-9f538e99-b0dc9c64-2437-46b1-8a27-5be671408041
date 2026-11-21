"""FastAPI application wiring.

``uvicorn app.main:app`` (used by docker compose) or
``uvicorn app.main:create_app --factory`` both work.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .api import router
from .engine import Engine
from .errors import RehearsalError
from .store import MemoryStore, SqliteStore, Store

DEFAULT_DB_PATH = os.environ.get("DB_PATH", "data/migrations.db")


def create_app(store: Store | None = None) -> FastAPI:
    """Build the application around ``store`` (in-memory unless given)."""
    engine = Engine(store or SqliteStore(DEFAULT_DB_PATH))

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        engine.initialize()
        yield
        engine.close()

    app = FastAPI(
        title="Circuit Migration Rehearsal",
        version="1.0.0",
        description=(
            "Rehearse migrating tunnels onto new paths before touching production. "
            "Submit a directed topology, 2-10 tunnels and each tunnel's current and "
            "target path; the service returns a rehearsal id and a fully feasible "
            "migration order, or refuses to start one."
        ),
        lifespan=lifespan,
    )
    app.state.engine = engine

    @app.exception_handler(RehearsalError)
    async def _domain_error(_: Request, exc: RehearsalError) -> JSONResponse:
        body: dict = {"error": exc.code, "detail": str(exc)}
        errors = getattr(exc, "errors", None)
        if errors is not None:
            body["errors"] = errors
        return JSONResponse(status_code=exc.status_code, content=body)

    app.include_router(router)

    @app.get("/health", tags=["ops"], summary="Liveness probe")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


def create_memory_app() -> FastAPI:
    """Factory that keeps everything in memory (handy for tests)."""
    return create_app(MemoryStore())


app = create_app()
