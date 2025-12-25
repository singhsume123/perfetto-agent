"""CLI entry point for Perfetto Baseline Analyzer."""

import typer
from pathlib import Path
from rich.console import Console
from perfetto_agent.analyzer import analyze_trace

app = typer.Typer(
    help="Perfetto Baseline Analyzer - Analyze Android performance traces",
    no_args_is_help=True
)
console = Console()


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context):
    """Perfetto Baseline Analyzer - Analyze Android performance traces."""
    if ctx.invoked_subcommand is None:
        # Show help if no subcommand is provided
        pass


@app.command()
def analyze(
    trace: Path = typer.Option(..., "--trace", help="Path to Perfetto trace file"),
    out: Path = typer.Option("analysis.json", "--out", help="Output JSON file path"),
    long_task_ms: int = typer.Option(50, "--long-task-ms", help="Threshold for long tasks in milliseconds"),
    top_n: int = typer.Option(5, "--top-n", help="Number of top long tasks to report"),
):
    """Analyze a Perfetto trace and generate analysis.json."""

    # Validate trace file exists
    if not trace.exists():
        console.print(f"[red]Error:[/red] Trace file not found: {trace}")
        raise typer.Exit(code=1)

    if not trace.is_file():
        console.print(f"[red]Error:[/red] Path is not a file: {trace}")
        raise typer.Exit(code=1)

    console.print(f"[blue]Analyzing trace:[/blue] {trace}")
    console.print(f"[blue]Output file:[/blue] {out}")
    console.print(f"[blue]Long task threshold:[/blue] {long_task_ms}ms")
    console.print(f"[blue]Top N tasks:[/blue] {top_n}")

    # Run analysis
    try:
        result = analyze_trace(
            trace_path=str(trace),
            long_task_ms=long_task_ms,
            top_n=top_n
        )

        # Write output
        import json
        with open(out, 'w') as f:
            json.dump(result, f, indent=2)

        console.print(f"[green]✓[/green] Analysis complete: {out}")

    except Exception as e:
        console.print(f"[red]Error during analysis:[/red] {e}")
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
