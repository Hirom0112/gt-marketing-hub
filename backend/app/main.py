"""FastAPI application entrypoint + AWS Lambda handler.

S0 wires the read-only landing surface (ARCHITECTURE.md §6): the deterministic
pipeline + Family Record GET endpoints, served over the in-memory repository
(ASSUMPTIONS A-3). The AI edge and write paths arrive in later slices.
"""

from fastapi import FastAPI
from mangum import Mangum

from app.api.families import router as families_router

app = FastAPI(title="GT Growth Cockpit", version="0.1.0")


@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe — returns 200 with a fixed status body."""
    return {"status": "ok"}


# Read-only landing API (FR-2.1/2.2) — /pipeline, /families, /families/{id}.
app.include_router(families_router)


# AWS Lambda + API Gateway entrypoint (ARCHITECTURE.md §12).
handler = Mangum(app)
