#!/usr/bin/env python
"""Audit one local invoice file."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Annotated

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import typer  # noqa: E402
from rich.console import Console  # noqa: E402

from src.config import ConfigurationError  # noqa: E402
from src.invoices.file_loader import InvoiceLoadError, load_invoice_path  # noqa: E402

app = typer.Typer(add_completion=False)
console = Console()


@app.command()
def main(
    invoice: Annotated[
        Path,
        typer.Option(help="Path to a TXT, PDF, or image invoice"),
    ],
    query: Annotated[
        str,
        typer.Option(help="The user question about this invoice"),
    ],
    deepseek: Annotated[
        bool,
        typer.Option(
            "--deepseek",
            help="Use DeepSeek for the explanation (requires DEEPSEEK_API_KEY)",
        ),
    ] = False,
) -> None:
    from src.assistant.pipeline import run_assistant

    try:
        loaded = load_invoice_path(invoice)
        response = run_assistant(loaded.text, query, use_llm=deepseek)
        response.warnings.extend(loaded.warnings)
    except (ConfigurationError, InvoiceLoadError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    console.print_json(json.dumps(response.model_dump(mode="json"), ensure_ascii=False))


if __name__ == "__main__":
    app()
