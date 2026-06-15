"""FastAPI application entrypoint + AWS Lambda handler.

Kept deliberately import-light: only what a bootable skeleton needs.
Feature routers (ARCHITECTURE.md §6) are wired in later slices.
"""

from fastapi import FastAPI
from mangum import Mangum

app = FastAPI(title="GT Growth Cockpit", version="0.1.0")


@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe — returns 200 with a fixed status body."""
    return {"status": "ok"}


# AWS Lambda + API Gateway entrypoint (ARCHITECTURE.md §12).
handler = Mangum(app)
