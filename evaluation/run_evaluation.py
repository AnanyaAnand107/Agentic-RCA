"""Evaluate saved Agentic-RCA outcomes against a labeled JSONL dataset.

Each line in the input should contain: incident_id, expected_commit, actual_commit,
reproduction_result, patch_passed, baseline_mttr_minutes, observed_mttr_minutes.
Missing measurements are excluded from that metric rather than treated as failures.
"""
from __future__ import annotations
import argparse, json
from pathlib import Path
from typing import Any


def evaluate(records: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(records)
    localization = [r for r in records if r.get("expected_commit") and r.get("actual_commit")]
    reproduction = [r for r in records if r.get("reproduction_result") is not None]
    patches = [r for r in records if r.get("patch_passed") is not None]
    mttr = [r for r in records if isinstance(r.get("baseline_mttr_minutes"), (int, float)) and isinstance(r.get("observed_mttr_minutes"), (int, float)) and r["baseline_mttr_minutes"] > 0]
    improved = [((r["baseline_mttr_minutes"] - r["observed_mttr_minutes"]) / r["baseline_mttr_minutes"]) * 100 for r in mttr]
    return {
        "dataset_size": total,
        "localization_accuracy": sum(r["expected_commit"] == r["actual_commit"] for r in localization) / len(localization) if localization else None,
        "localization_samples": len(localization),
        "reproduction_reliability": sum(str(r["reproduction_result"]).lower() in {"reproduced", "true", "passed"} for r in reproduction) / len(reproduction) if reproduction else None,
        "reproduction_samples": len(reproduction),
        "patch_pass_rate": sum(bool(r["patch_passed"]) for r in patches) / len(patches) if patches else None,
        "patch_samples": len(patches),
        "mean_mttr_improvement_pct": sum(improved) / len(improved) if improved else None,
        "mttr_samples": len(mttr),
        "notes": "Null means no labeled measurements were provided; this runner does not infer or fabricate results."
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path, help="Labeled JSONL input")
    parser.add_argument("--output", type=Path, help="Optional JSON report path")
    args = parser.parse_args()
    records = [json.loads(line) for line in args.dataset.read_text(encoding="utf-8").splitlines() if line.strip()]
    report = evaluate(records)
    rendered = json.dumps(report, indent=2)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)

if __name__ == "__main__":
    main()
