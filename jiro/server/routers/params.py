"""Live Parameter Layer endpoints (v0.4.0 Phase 2).

These endpoints expose versioned evidence sets: build from a packet, bind to a
model budget, decay freshness, diff two versions, and merge. They do not
update model weights.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from jiro.auth import AuthContext
from jiro.params.model import ParameterSet
from jiro.params.store import (
    ParameterStore,
    bind,
    build_from_packet,
    decay,
    diff,
    merge,
)
from jiro.server.deps import get_settings, require_feature

router = APIRouter(prefix="/v1/params", tags=["params"])


class FromPacketRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=128)
    packet: dict[str, Any]
    ttl_policy: Optional[dict[str, Any]] = None


class BindRequest(BaseModel):
    max_tokens: int = Field(1200, ge=64, le=32000)
    model_profile: str = Field("generic", max_length=64)


class DecayRequest(BaseModel):
    now: Optional[str] = None
    save: bool = False


class DiffRequest(BaseModel):
    from_id: str
    to_id: str


class MergeRequest(BaseModel):
    left_id: str
    right_id: str
    save: bool = True


def get_parameter_store(request: Request) -> ParameterStore:
    store = getattr(request.app.state, "parameter_store", None)
    if store is None:
        settings = get_settings(request)
        root = Path(str(settings.get("params.root", "~/.jiro/params"))).expanduser()
        store = ParameterStore(root)
        request.app.state.parameter_store = store
    return store


def _dump(param_set: ParameterSet) -> dict[str, Any]:
    return param_set.model_dump(mode="json", by_alias=True)


@router.post("/from-packet")
async def create_from_packet(
    body: FromPacketRequest,
    ctx: AuthContext = Depends(require_feature("basic_search")),
    store: ParameterStore = Depends(get_parameter_store),
) -> dict[str, Any]:
    try:
        param_set = build_from_packet(
            body.packet,
            body.session_id,
            ttl_policy=body.ttl_policy,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    store.save(param_set)
    return _dump(param_set)


@router.get("")
async def list_sets(
    session_id: Optional[str] = None,
    limit: int = 50,
    ctx: AuthContext = Depends(require_feature("basic_search")),
    store: ParameterStore = Depends(get_parameter_store),
) -> dict[str, Any]:
    sets = store.list(session_id)[-max(1, min(limit, 200)):]
    return {"sets": [_dump(item) for item in sets], "count": len(sets)}


@router.get("/{set_id}")
async def get_set(
    set_id: str,
    ctx: AuthContext = Depends(require_feature("basic_search")),
    store: ParameterStore = Depends(get_parameter_store),
) -> dict[str, Any]:
    try:
        return _dump(store.load(set_id))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="parameter set not found") from exc


@router.delete("/{set_id}")
async def delete_set(
    set_id: str,
    ctx: AuthContext = Depends(require_feature("basic_search")),
    store: ParameterStore = Depends(get_parameter_store),
) -> dict[str, Any]:
    return {"deleted": store.delete(set_id), "id": set_id}


@router.post("/{set_id}/bind")
async def bind_set(
    set_id: str,
    body: BindRequest,
    ctx: AuthContext = Depends(require_feature("basic_search")),
    store: ParameterStore = Depends(get_parameter_store),
) -> dict[str, Any]:
    try:
        param_set = store.load(set_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="parameter set not found") from exc
    try:
        binding = bind(
            param_set,
            max_tokens=body.max_tokens,
            model_profile=body.model_profile,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    payload = binding.model_dump(mode="json", by_alias=True)
    payload["prompt"] = binding.to_prompt()
    return payload


@router.post("/{set_id}/decay")
async def decay_set(
    set_id: str,
    body: DecayRequest,
    ctx: AuthContext = Depends(require_feature("basic_search")),
    store: ParameterStore = Depends(get_parameter_store),
) -> dict[str, Any]:
    try:
        param_set = store.load(set_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="parameter set not found") from exc
    now = None
    if body.now:
        from datetime import datetime

        try:
            now = datetime.fromisoformat(body.now.replace("Z", "+00:00"))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="now must be ISO-8601") from exc
    updated = decay(param_set, now=now)
    if body.save:
        store.save(updated)
    return _dump(updated)


@router.post("/diff")
async def diff_sets(
    body: DiffRequest,
    ctx: AuthContext = Depends(require_feature("basic_search")),
    store: ParameterStore = Depends(get_parameter_store),
) -> dict[str, Any]:
    try:
        old = store.load(body.from_id)
        new = store.load(body.to_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="parameter set not found") from exc
    return diff(old, new)


@router.post("/merge")
async def merge_sets(
    body: MergeRequest,
    ctx: AuthContext = Depends(require_feature("basic_search")),
    store: ParameterStore = Depends(get_parameter_store),
) -> dict[str, Any]:
    try:
        a = store.load(body.left_id)
        b = store.load(body.right_id)
        merged = merge(a, b)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="parameter set not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if body.save:
        store.save(merged)
    return _dump(merged)
