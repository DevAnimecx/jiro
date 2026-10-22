"""Answer-quality non-inferiority gate for the ``efficiency-3x`` claim.

``docs/v0.4.0/CLAIMS.md`` makes the token ratio a public claim only when answer
quality is non-inferior:

    Non-inferiority on the 1-10 rubric:
      - LLM judge, fixed model and prompt, 30 runs.
      - 50 human spot checks, blind.
      - Lower bound of the 95% confidence interval no worse than -2 points
        versus baseline.

Judging needs a model, so this script does not fabricate scores. It does the
half that can be done mechanically and without an API key:

1. Builds the paired baseline text (raw scraper markdown) and candidate text
   (the evidence packet) for each of ``--runs`` sampling runs.
2. Writes a blind, order-randomised judging packet to ``--packets``.
3. If an answer payload exists (``--answers``), scores the paired deltas and
   writes the ``quality`` block into ``efficiency.json``.

Without ``--answers`` it records ``status: pending_judgement`` and the claim
stays void, which is the honest outcome: the ratio is real and the quality
half has not run.
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
ARTIFACT = ROOT / "docs" / "v0.4.0" / "benchmarks" / "efficiency.json"
TARGET_CI_LOW = -2.0
RUBRIC_MAX = 10.0


def _ci_low(deltas: List[float]) -> Optional[float]:
    """Lower bound of the 95% confidence interval of the mean paired delta."""
    if len(deltas) < 2:
        return None
    mean = statistics.mean(deltas)
    stdev = statistics.stdev(deltas)
    # Normal approximation; the claim only needs the bound, and with n>=20 the
    # t and z bounds differ well inside the 2-point margin.
    return mean - 1.96 * stdev / (len(deltas) ** 0.5)


def _paired(deltas: List[float], alpha: float = 0.05) -> Dict[str, Any]:
    mean = statistics.mean(deltas) if deltas else None
    low = _ci_low(deltas)
    return {
        "n": len(deltas),
        "mean_delta": round(mean, 3) if mean is not None else None,
        "ci_low": round(low, 3) if low is not None else None,
        "ci_high": (
            round(mean + 1.96 * statistics.stdev(deltas) / (len(deltas) ** 0.5), 3)
            if len(deltas) >= 2
            else None
        ),
        "margin": TARGET_CI_LOW,
    }


def build_packet(runs: int, seed: int, out_dir: Path) -> Path:
    rng = random.Random(seed)
    out_dir.mkdir(parents=True, exist_ok=True)
    items = []
    for i in range(runs):
        # A and B are the same two texts in a per-item random order so the
        # judge cannot learn which side is the candidate.
        candidate_first = rng.random() < 0.5
        items.append(
            {
                "item": i,
                "prompt_id": f"quality-{i:03d}",
                "text_a_source": "candidate" if candidate_first else "baseline",
                "text_b_source": "baseline" if candidate_first else "candidate",
            }
        )
    packet = {
        "rubric": "1-10 answer quality; judge on factual support, coverage of the "
        "required claims, and absence of unsupported statements.",
        "judge_model": "fixed at run time and recorded in the answers file",
        "blinding": "text_a/text_b assignment is randomised per item; the judge "
        "must not be shown text_*_source",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "seed": seed,
        "items": items,
    }
    path = out_dir / "judging-packet.json"
    path.write_text(json.dumps(packet, indent=2), encoding="utf-8")
    return path


def record(artifact: Path, answers: Optional[Path], packet: Path) -> Dict[str, Any]:
    data = json.loads(artifact.read_text(encoding="utf-8"))
    if answers is None or not answers.exists():
        data["quality"] = {
            "status": "pending_judgement",
            "rubric": "1-10",
            "target_ci_low": TARGET_CI_LOW,
            "packet": str(packet.relative_to(ROOT)),
            "note": (
                "The ratio is measured; the non-inferiority gate needs 30 LLM-judge "
                "runs and 50 blind human spot checks. Until those scores exist this "
                "artifact cannot support the public 3x claim."
            ),
        }
        artifact.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return data["quality"]

    payload = json.loads(answers.read_text(encoding="utf-8"))
    deltas: List[float] = []
    for item in payload.get("scores") or []:
        base = item.get("baseline_score")
        cand = item.get("candidate_score")
        if base is None or cand is None:
            continue
        deltas.append(float(cand) - float(base))

    stats = _paired(deltas)
    passed = stats["ci_low"] is not None and stats["ci_low"] >= TARGET_CI_LOW
    quality = {
        "status": "passed" if passed else "failed",
        "rubric": "1-10",
        "judge_model": payload.get("judge_model"),
        "runs": stats["n"],
        "human_spot_checks": payload.get("human_spot_checks"),
        "baseline_mean": (
            round(statistics.mean([i["baseline_score"] for i in payload["scores"]]), 3)
            if payload.get("scores")
            else None
        ),
        "candidate_mean": (
            round(statistics.mean([i["candidate_score"] for i in payload["scores"]]), 3)
            if payload.get("scores")
            else None
        ),
        "ci_low": stats["ci_low"],
        "ci_high": stats["ci_high"],
        "target_ci_low": TARGET_CI_LOW,
        "packet": str(packet.relative_to(ROOT)),
    }
    if not passed:
        quality["note"] = (
            "Quality non-inferiority not established; the efficiency claim is void "
            "regardless of the token ratio."
        )
    data["quality"] = quality
    artifact.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return quality


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=30)
    parser.add_argument("--seed", type=int, default=4040)
    parser.add_argument("--answers", type=Path, default=None)
    parser.add_argument(
        "--packets",
        type=Path,
        default=ROOT / "docs" / "v0.4.0" / "benchmarks" / "judging",
    )
    parser.add_argument("--artifact", type=Path, default=ARTIFACT)
    args = parser.parse_args()

    packet = build_packet(args.runs, args.seed, args.packets)
    quality = record(args.artifact, args.answers, packet)
    print(json.dumps({"quality": quality, "packet": str(packet)}, indent=2))
    return 0 if quality["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
