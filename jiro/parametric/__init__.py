"""Evidence packet compilation (v0.4.0 Phase 2).

The packet is the unit agents bind to a model call: token-bounded, cited,
deterministic, and explicit about what was omitted.
"""

from jiro.parametric.packet import (
    Claim,
    EvidencePacket,
    Source,
    build_packet,
    validate_packet,
)

__all__ = ["Claim", "EvidencePacket", "Source", "build_packet", "validate_packet"]
