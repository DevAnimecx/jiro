"""Release gate over ``benchmarks/claims.yaml``.

Every public performance number must be backed by a committed artifact, and a
claim whose quality gate has not run may only be published when the registry
carries an explicit caveat. These tests pin both rules against the shipped
registry so a future edit cannot quietly publish an ungated number.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "benchmarks"))

import report as report_mod  # noqa: E402

CLAIMS = ROOT / "benchmarks" / "claims.yaml"


def _registry():
    return yaml.safe_load(CLAIMS.read_text(encoding="utf-8"))


def _check(claim, artifact_data):
    """Run report.py's claim check against an in-memory artifact."""
    return report_mod.check_claim(claim, artifact_data)


def test_registry_loads_and_every_artifact_exists():
    reg = _registry()
    claims = reg["claims"]
    assert claims, "registry has no claims"
    for claim in claims:
        path = ROOT / claim["artifact"]
        assert path.exists(), f"{claim['id']}: missing artifact {claim['artifact']}"
        json.loads(path.read_text(encoding="utf-8"))


def test_all_shipped_claims_pass_the_release_gate():
    reg = _registry()
    for claim in reg["claims"]:
        data = json.loads((ROOT / claim["artifact"]).read_text(encoding="utf-8"))
        result = _check(claim, data)
        assert result["ok"], f"{claim['id']} failed: {result['reason']}"


def test_ungated_claim_without_a_caveat_is_blocked():
    """The gate must block publication when no caveat is declared."""
    claim = {
        "id": "efficiency-3x",
        "metric": "tokens_per_verified_citation",
        "target_ratio": 3.0,
        "quality_gate": "required",
        "workload": "research_5q_10src",
    }
    data = {
        "metrics": {"ratio": 59.188},
        "quality": {"status": "pending_judgement"},
        "workload": "research_5q_10src",
    }
    result = _check(claim, data)
    assert result["ok"] is False
    assert "quality gate" in result["reason"]


def test_ungated_claim_with_a_caveat_publishes_and_surfaces_it():
    claim = {
        "id": "efficiency-3x",
        "metric": "tokens_per_verified_citation",
        "target_ratio": 3.0,
        "quality_gate": "required",
        "workload": "research_5q_10src",
        "caveat": "gate pending",
    }
    data = {
        "metrics": {"ratio": 59.188},
        "quality": {"status": "pending_judgement"},
        "workload": "research_5q_10src",
    }
    result = _check(claim, data)
    assert result["ok"] is True
    assert result["caveat"] == "gate pending"


def test_stale_caveat_on_a_passed_gate_is_rejected():
    """A caveat left in place after the gate passes must fail the release."""
    claim = {
        "id": "efficiency-3x",
        "metric": "tokens_per_verified_citation",
        "target_ratio": 3.0,
        "quality_gate": "required",
        "workload": "research_5q_10src",
        "caveat": "gate pending",
    }
    data = {
        "metrics": {"ratio": 59.188},
        "quality": {"status": "passed"},
        "workload": "research_5q_10src",
    }
    result = _check(claim, data)
    assert result["ok"] is False
    assert "stale caveat" in result["reason"]


def test_ratio_below_target_fails_even_with_a_caveat():
    """A caveat excuses a pending quality gate, never a missed target."""
    claim = {
        "id": "efficiency-3x",
        "metric": "tokens_per_verified_citation",
        "target_ratio": 3.0,
        "quality_gate": "required",
        "workload": "research_5q_10src",
        "caveat": "gate pending",
    }
    data = {
        "metrics": {"ratio": 1.2},
        "quality": {"status": "pending_judgement"},
        "workload": "research_5q_10src",
    }
    result = _check(claim, data)
    assert result["ok"] is False
    assert "ratio" in result["reason"]


def test_published_efficiency_claim_states_its_caveat():
    """The shipped artifact and registry must agree on the caveat."""
    reg = _registry()
    claim = next(c for c in reg["claims"] if c["id"] == "efficiency-3x")
    assert claim.get("caveat"), "the published efficiency claim needs a caveat"

    data = json.loads((ROOT / claim["artifact"]).read_text(encoding="utf-8"))
    assert data["quality"]["status"] == "pending_judgement"
    assert data["quality"].get("caveat")
    assert data["quality"].get("publication") == "published_with_caveat"
