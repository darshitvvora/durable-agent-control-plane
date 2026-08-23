"""FastAPI app — the sole Temporal client the UI talks to (CLAUDE.md §2).

Run with: uv run uvicorn app.api.main:app --reload
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes.agents import router as agents_router
from app.api.routes.demo import router as demo_router
from app.api.routes.events import router as events_router
from app.api.routes.jobs import router as jobs_router
from app.api.routes.metrics import router as metrics_router
from app.api.routes.tenants import router as tenants_router
from app.temporal_client import connect


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # One shared client for the process's lifetime — the events route's
    # WorkflowStreamClient needs a real Client, not a fresh connection per
    # SSE subscriber.
    app.state.temporal_client = await connect()
    yield


app = FastAPI(title="Durable Agent Control Plane API", lifespan=lifespan)

app.include_router(tenants_router)
app.include_router(agents_router)
app.include_router(jobs_router)
app.include_router(metrics_router)
app.include_router(demo_router)
app.include_router(events_router)


@app.get("/health")
def health() -> dict:
    return {"ok": True}
