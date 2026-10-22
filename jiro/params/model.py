"""Parameter set contracts."""

from __future__ import annotations

from typing import Any, Dict, Tuple

from pydantic import BaseModel, ConfigDict, Field

from jiro.parametric.packet import Claim, Source

BINDING_SCHEMA = "jiro.binding/v1"
PARAM_SCHEMA = "jiro.params/v1"


class ParameterSet(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    param_schema: str = Field(PARAM_SCHEMA, alias="schema")
    id: str
    session_id: str
    version: int = Field(1, ge=1)
    created_at: str
    claims: Tuple[Claim, ...] = ()
    sources: Tuple[Source, ...] = ()
    ttl_policy: Dict[str, Any] = Field(default_factory=dict)
    freshness: Dict[str, float] = Field(default_factory=dict)
    provenance_digest: str

    def claim_count(self) -> int:
        return len(self.claims)


class Binding(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    binding_schema: str = Field(BINDING_SCHEMA, alias="schema")
    set_id: str
    version: int
    model_profile: str
    max_tokens: int
    used_tokens: int
    claims: Tuple[Dict[str, Any], ...]
    sources: Tuple[Dict[str, Any], ...]
    freshness: Dict[str, float] = Field(default_factory=dict)
    notes: Tuple[str, ...] = ()

    def to_prompt(self) -> str:
        lines = [f"Evidence set {self.set_id} v{self.version} ({self.used_tokens} tokens)"]
        for claim in self.claims:
            cites = " ".join(f"[{sid}]" for sid in claim.get("source_ids") or [])
            lines.append(f"- {claim.get('text', '')} {cites}".rstrip())
        if self.sources:
            lines.append("Sources:")
            for source in self.sources:
                lines.append(f"- [{source.get('id')}] {source.get('url')}")
        return "\n".join(lines)


class WatchSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    query: str = Field(..., min_length=1, max_length=500)
    session_id: str
    interval_seconds: int = Field(21600, ge=60, le=2_592_000)
    enabled: bool = True
    created_at: str
    last_run_at: str | None = None
    last_param_id: str | None = None
    ttl_policy: Dict[str, Any] = Field(default_factory=dict)
