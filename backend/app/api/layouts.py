"""Per-user Home layout endpoints — the composable-Home GET/PUT seam (B3).

The composition layer that wires the B3 layout store (``app.data.layouts_store``),
the server-side widget registry (``app.core.widget_registry``), and the pure
starter-pack reconcile (``app.core.dashboard_layout.merge_starter_pack``) behind
REST. Thin by design: the merge is pure/owned core (INV-2); this router only loads
the saved layout, runs the reconcile, and persists writes.

  ``GET  /home/layout``
    The reconciled Home layout for the VERIFIED principal. Loads the saved RGL
    placements (``None`` for a new user), then returns
    ``merge_starter_pack(saved, registry_ids=REGISTRY_IDS, starter_ids=STARTER_IDS)``
    — so a new user gets the starter pack, a placement whose widget id has since
    been removed is dropped, and a missing starter widget is re-hydrated.

  ``PUT  /home/layout``
    Save the principal's layout. Persists the body's placement array under the
    principal's ``user_id`` (UPSERT) and returns the reconciled result, so the
    client immediately sees the same drop/re-hydrate the next GET would.

**Scoping (the IDOR property at the app layer).** Both routes key off the VERIFIED
principal's ``user_id`` (the JWT ``sub``) — there is NO ``owner`` query param. A
user can only read/write THEIR OWN layout; RLS on the 0029 table is the DB backstop
(INV-5). A token without a ``sub`` (``principal.user_id is None``) cannot be
persisted to: GET returns the merged-empty starter pack WITHOUT persisting, and PUT
is a 422 no-op (there is no row to own). These are the documented, boring choices.

This module may import ``app.core`` (it is the composition root); ``app/core/``
stays pure. No live external send is ever made here.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import Principal, get_layouts_store, get_principal
from app.core.dashboard_layout import merge_starter_pack
from app.core.widget_registry import REGISTRY_IDS, STARTER_IDS
from app.data.layouts_store import LayoutsStore

router = APIRouter(tags=["home"])

# Dependency aliases (Annotated keeps the call in the type — ruff B008; the idiomatic
# FastAPI style matching app/api/decisions.py).
StoreDep = Annotated[LayoutsStore, Depends(get_layouts_store)]
PrincipalDep = Annotated[Principal, Depends(get_principal)]

# A placement is a loose RGL dict (``{"i", "x", "y", "w", "h"}``); the merge owns the
# id semantics, so the wire shape stays permissive.
Placement = dict[str, Any]

# The registry/starter sets are frozensets (immutable, INV-11); merge_starter_pack
# takes plain sets, so widen once here (a copy — never mutated).
_REGISTRY_IDS: set[str] = set(REGISTRY_IDS)
_STARTER_IDS: set[str] = set(STARTER_IDS)


def _merged(saved: list[Placement] | None) -> list[Placement]:
    """Reconcile a saved layout against the live registry (the pure core call)."""
    return merge_starter_pack(saved, registry_ids=_REGISTRY_IDS, starter_ids=_STARTER_IDS)


class LayoutBody(BaseModel):
    """Body for ``PUT /home/layout`` — the operator's arranged placement array."""

    layout: list[Placement] = Field(default_factory=list)


@router.get("/home/layout", response_model=list[Placement])
def get_home_layout(store: StoreDep, principal: PrincipalDep) -> list[Placement]:
    """The reconciled Home layout for the verified principal (starter pack for a new user).

    A token without a ``sub`` (``user_id is None``) cannot own a row, so it gets the
    merged-empty starter pack WITHOUT a load or a persist (documented).
    """
    if principal.user_id is None:
        return _merged(None)
    saved = store.get_layout(principal.user_id)
    return _merged(saved)


@router.put("/home/layout", response_model=list[Placement])
def put_home_layout(body: LayoutBody, store: StoreDep, principal: PrincipalDep) -> list[Placement]:
    """Persist the principal's layout (UPSERT) and return the reconciled result.

    A token without a ``sub`` (``user_id is None``) has no row to own, so the save
    is a 422 no-op (you cannot persist a layout without a user — the boring choice).
    """
    if principal.user_id is None:
        raise HTTPException(status_code=422, detail="cannot persist a layout without a user id")
    store.put_layout(principal.user_id, body.layout)
    # Re-read so the response reflects exactly what a subsequent GET would serve.
    return _merged(store.get_layout(principal.user_id))
