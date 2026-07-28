#!/usr/bin/env python
"""Run the deterministic evaluation harness, optionally with DeepSeek prose."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Annotated

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import typer  # noqa: E402
from rich.console import Console  # noqa: E402
from rich.table import Table  # noqa: E402

from src.config import BASELINE_PATH, ConfigurationError, get_settings  # noqa: E402
from src.evals.runner import check_regression, load_cases, run_eval  # noqa: E402

app = typer.Typer(add_completion=False)
console = Console()


@app.command()
def main(
    update_baseline: Annotated[bool, typer.Option("--update-baseline")] = False,
    baseline: Annotated[Path | None, typer.Option("--baseline")] = None,
    fail_on_regression: Annotated[bool, typer.Option("--fail-on-regression")] = False,
    category: Annotated[str | None, typer.Option("--category")] = None,
    case_id: Annotated[str | None, typer.Option("--case-id")] = None,
    limit: Annotated[int | None, typer.Option("--limit")] = None,
    resume_dir: Annotated[
        Path | None,
        typer.Option("--resume-dir", help="Existing run directory to resume from"),
    ] = None,
    deepseek: Annotated[
        bool,
        typer.Option(
            "--deepseek",
            help="Use DeepSeek for answer prose; offline mode is the default",
        ),
    ] = False,
) -> None:
    if deepseek:
        try:
            get_settings().require_api_key()
        except ConfigurationError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1) from exc

    cases = load_cases(category=category, case_id=case_id, limit=limit)
    if not cases:
        console.print("[red]No eval cases matched the given filters.[/red]")
        raise typer.Exit(code=1)

    mode = "DeepSeek" if deepseek else "offline"
    console.print(f"Running {len(cases)} eval case(s) sequentially in {mode} mode...")
    outcome = run_eval(cases, run_dir=resume_dir, use_llm=deepseek)
    summary = outcome["summary"]

    table = Table(title="Eval Summary")
    table.add_column("Metric")
    table.add_column("Value")
    table.add_row("Total cases", str(summary["total_cases"]))
    table.add_row("Mean score", str(summary["mean_score"]))
    table.add_row("Pass rate", str(summary["pass_rate"]))
    table.add_row("P0 failures", str(summary["p0_failures"] or "none"))
    for cat, score in summary["category_scores"].items():
        table.add_row(f"Category: {cat}", str(score))
    console.print(table)
    console.print(f"Results written to {outcome['run_dir']}")

    if update_baseline:
        BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
        BASELINE_PATH.write_text(
            json.dumps(
                {
                    "mode": mode.lower(),
                    "summary": summary,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        console.print(f"[green]Baseline updated -> {BASELINE_PATH}[/green]")

    if baseline:
        ok, problems = check_regression(outcome, baseline)
        if ok:
            console.print("[green]No regression against baseline.[/green]")
        else:
            for problem in problems:
                console.print(f"[red]REGRESSION: {problem}[/red]")
            if fail_on_regression:
                raise typer.Exit(code=2)
    elif summary["p0_failures"] and fail_on_regression:
        console.print("[red]P0 failures always fail the run.[/red]")
        raise typer.Exit(code=2)


if __name__ == "__main__":
    app()
