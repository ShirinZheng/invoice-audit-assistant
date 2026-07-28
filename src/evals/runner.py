"""Sequential, resumable, regression-aware evaluation runner."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from src.config import EVAL_CASES_PATH, INVOICE_DIR, RUNS_DIR, get_settings
from src.evals.graders import grade_case
from src.rag.retriever import Retriever


def load_cases(
    cases_path: Path = EVAL_CASES_PATH,
    category: str | None = None,
    case_id: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    if case_id:
        cases = [c for c in cases if c["case_id"] == case_id]
    if category:
        cases = [c for c in cases if c["category"] == category]
    if limit:
        cases = cases[:limit]
    return cases


def run_eval(
    cases: list[dict[str, Any]],
    run_dir: Path | None = None,
    resume: bool = True,
    use_llm: bool = False,
) -> dict[str, Any]:
    """Run eval cases sequentially. Per-case results are written incrementally
    so an interrupted run can resume from partial results."""
    from src.assistant.pipeline import run_assistant

    retriever = Retriever()

    run_dir = run_dir or RUNS_DIR / f"eval-{time.strftime('%Y%m%d-%H%M%S')}"
    run_dir.mkdir(parents=True, exist_ok=True)
    partial_path = run_dir / "case_results.jsonl"

    done: dict[str, dict[str, Any]] = {}
    if resume and partial_path.exists():
        for line in partial_path.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            done[record["case_id"]] = record

    results: list[dict[str, Any]] = []
    with partial_path.open("a", encoding="utf-8") as partial:
        for case in cases:
            if case["case_id"] in done:
                results.append(done[case["case_id"]])
                continue

            record: dict[str, Any] = {
                "case_id": case["case_id"],
                "category": case["category"],
                "priority": case.get("priority", "P1"),
            }
            try:
                invoice_path = INVOICE_DIR / case["invoice_file"]
                invoice_text = invoice_path.read_text(encoding="utf-8")
                response = run_assistant(
                    invoice_text,
                    case["query"],
                    retriever=retriever,
                    use_llm=use_llm,
                )
                grade = grade_case(case, response)
                # Raw parsed output stored separately from the grade for debugging.
                record["response"] = response.model_dump(mode="json")
                record.update(grade)
            except Exception as exc:  # noqa: BLE001 — record and continue
                record.update(
                    {
                        "score": 0.0,
                        "passed": False,
                        "p0_failed": case.get("priority") == "P0",
                        "checks": {},
                        "error": str(exc),
                    }
                )
            partial.write(json.dumps(record, ensure_ascii=False) + "\n")
            partial.flush()
            results.append(record)

    summary = summarize(results)
    (run_dir / "results.json").write_text(
        json.dumps({"summary": summary, "results": results}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {"summary": summary, "results": results, "run_dir": str(run_dir)}


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(results)
    mean_score = sum(r["score"] for r in results) / total if total else 0.0
    by_category: dict[str, list[float]] = {}
    for r in results:
        by_category.setdefault(r["category"], []).append(r["score"])
    return {
        "total_cases": total,
        "mean_score": round(mean_score, 4),
        "pass_rate": round(sum(1 for r in results if r["passed"]) / total, 4) if total else 0.0,
        "p0_failures": [r["case_id"] for r in results if r.get("p0_failed")],
        "category_scores": {
            cat: round(sum(scores) / len(scores), 4) for cat, scores in by_category.items()
        },
    }


def check_regression(
    current: dict[str, Any], baseline_path: Path, threshold: float | None = None
) -> tuple[bool, list[str]]:
    """Compare against a baseline. Returns (ok, problems).

    P0 failures always fail the run. Mean-score drops beyond the threshold
    (overall or per category) fail the run.
    """
    threshold = threshold if threshold is not None else get_settings().eval_regression_threshold
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))["summary"]
    summary = current["summary"]
    problems: list[str] = []

    if summary["p0_failures"]:
        problems.append(f"P0 failures: {summary['p0_failures']}")

    drop = baseline["mean_score"] - summary["mean_score"]
    if drop > threshold:
        problems.append(
            f"Mean score regressed by {drop:.4f} "
            f"({baseline['mean_score']} -> {summary['mean_score']}), threshold {threshold}"
        )

    for cat, base_score in baseline.get("category_scores", {}).items():
        cur_score = summary.get("category_scores", {}).get(cat)
        if cur_score is not None and base_score - cur_score > threshold:
            problems.append(
                f"Category '{cat}' regressed by {base_score - cur_score:.4f} "
                f"({base_score} -> {cur_score})"
            )
    return (not problems, problems)
