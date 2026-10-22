"""Validate committed benchmark artifacts against ``claims.yaml``.

Usage:
    python benchmarks/report.py --strict

This is the release gate. It never estimates or fills missing numbers: a
missing or stale artifact fails the claim.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import yaml
except Exception as exc:  # pragma: no cover - dependency error
    print(f"error: PyYAML is required to read claims.yaml: {exc}", file=sys.stderr)
    raise SystemExit(2) from exc

ROOT = Path(__file__).resolve().parents[1]
CLAIMS = ROOT / "benchmarks" / "claims.yaml"


def _load(path: Path) -> Dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"error: cannot read {path}: {exc}", file=sys.stderr)
        return None


def check_claim(claim: Dict[str, Any],
                data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Evaluate one claim, loading its artifact unless one is supplied.

    Passing ``data`` lets the gate rules be exercised against in-memory
    artifacts, including ones that do not exist on disk.
    """
    if data is None:
        artifact = ROOT / str(claim.get("artifact", ""))
        data = _load(artifact)
        label = (
            str(artifact.relative_to(ROOT))
            if artifact.is_relative_to(ROOT)
            else str(artifact)
        )
    else:
        label = str(claim.get("artifact") or "<in-memory>")
    result: Dict[str, Any] = {
        "id": claim.get("id"),
        "artifact": label,
        "exists": data is not None,
        "ok": False,
        "reason": "",
    }
    if data is None:
        result["reason"] = "artifact missing"
        return result
    return _evaluate(claim, data, result)


def _check_claim(claim: Dict[str, Any]) -> Dict[str, Any]:
    return check_claim(claim)


def _evaluate(claim: Dict[str, Any], data: Dict[str, Any],
              result: Dict[str, Any]) -> Dict[str, Any]:

    metrics = data.get("metrics") or {}
    metric = claim.get("metric")
    if "target_ratio" in claim:
        ratio = metrics.get("ratio")
        result["value"] = ratio
        result["target"] = claim["target_ratio"]
        if ratio is None:
            result["reason"] = "ratio not measured"
        elif float(ratio) < float(claim["target_ratio"]):
            result["reason"] = f"ratio {ratio} < target {claim['target_ratio']}"
        else:
            result["ok"] = True
    elif "target" in claim:
        value = None
        if metric == "non_gap_claims_with_source_ratio":
            value = metrics.get("ratio")
        elif metric == "tier_a_clients_passing":
            value = metrics.get("passing")
        else:
            value = metrics.get(metric)
        result["value"] = value
        result["target"] = claim["target"]
        if value is None:
            result["reason"] = f"{metric} not measured"
        elif float(value) < float(claim["target"]):
            result["reason"] = f"{metric}={value} < target {claim['target']}"
        else:
            result["ok"] = True
    else:
        result["ok"] = True

    if claim.get("quality_gate") == "required":
        quality = data.get("quality") or {}
        status = quality.get("status", "missing")
        result["quality"] = status
        if status != "passed":
            # A claim may be published while its quality gate is still pending
            # only if the registry carries an explicit caveat, which is then
            # surfaced here and must travel with the number wherever it is
            # used. Without a caveat the gate blocks publication.
            #
            # A caveat excuses a gate that has not run. It never excuses a
            # measured value that missed its target, so this stands down when
            # the metric check above already failed.
            if claim.get("caveat") and result["ok"]:
                result["caveat"] = claim["caveat"]
                result["reason"] = "published with caveat"
            elif result["ok"]:
                result["ok"] = False
                result["reason"] = (
                    f"quality gate {status}; "
                    "a raw efficiency number is not a public claim"
                )
            else:
                # The metric already failed; keep that reason, which is the
                # actionable one, and record the gate alongside it.
                result["reason"] = f"{result['reason']}; quality gate {status}"
        elif claim.get("caveat"):
            # The gate now passes, so the caveat is stale and must be removed.
            result["ok"] = False
            result["reason"] = (
                "quality gate passed but a caveat is still declared; "
                "remove the stale caveat from claims.yaml"
            )

    if data.get("workload") != claim.get("workload") and claim.get("workload"):
        result["ok"] = False
        result["reason"] = f"workload {data.get('workload')!r} != {claim.get('workload')!r}"

    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strict", action="store_true", help="exit 1 on any failed claim")
    parser.add_argument("--json", action="store_true", help="emit machine-readable output")
    args = parser.parse_args()

    if not CLAIMS.exists():
        print(f"error: missing {CLAIMS}", file=sys.stderr)
        return 2
    registry = yaml.safe_load(CLAIMS.read_text(encoding="utf-8")) or {}
    claims: List[Dict[str, Any]] = list(registry.get("claims") or [])
    results = [_check_claim(claim) for claim in claims]
    failed = [item for item in results if not item["ok"]]

    if args.json:
        print(json.dumps({"results": results, "failed": len(failed)}, indent=2))
    else:
        for item in results:
            status = "PASS" if item["ok"] else "FAIL"
            value = item.get("value")
            target = item.get("target")
            detail = f" value={value} target={target}" if target is not None else ""
            reason = f" — {item['reason']}" if item.get("reason") else ""
            print(f"{status:4} {item['id']}{detail}{reason}")

    if failed and args.strict:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
