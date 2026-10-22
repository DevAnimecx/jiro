"""Live Parameter Layer (v0.4.0 Phase 2).

A parameter set is a versioned, decaying, diffable snapshot of web evidence.
It is bound to a model call as a cited packet (or, experimentally, vectors).
It is not a weight update.
"""

from jiro.params.model import Binding, ParameterSet
from jiro.params.store import ParameterStore, build_from_packet, diff, merge

__all__ = [
    "Binding",
    "ParameterSet",
    "ParameterStore",
    "build_from_packet",
    "diff",
    "merge",
]
