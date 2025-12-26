"""CLI entry point for Perfetto Baseline Analyzer."""

import typer
from typing import Optional
from pathlib import Path
from rich.console import Console
from perfetto_agent.analyzer import analyze_trace
from perfetto_agent.explain import run_explain

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
    focus_process: Optional[str] = typer.Option(None, "--focus-process", help="Filter analysis to a specific process name"),
    schema_version: str = typer.Option("A2", "--schema-version", help="Schema version to emit in JSON"),
    explain: bool = typer.Option(False, "--explain", help="Generate LLM explanation output"),
    explain_out: Path = typer.Option("explanation.md", "--explain-out", help="Explanation Markdown output path"),
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
    console.print(f"[blue]Focus process:[/blue] {focus_process}")
    console.print(f"[blue]Schema version:[/blue] {schema_version}")
    if explain:
        console.print(f"[blue]Explain output:[/blue] {explain_out}")

    # Run analysis
    try:
        result = analyze_trace(
            trace_path=str(trace),
            long_task_ms=long_task_ms,
            top_n=top_n,
            focus_process=focus_process,
            schema_version=schema_version
        )

        # Write output
        import json
        with open(out, 'w') as f:
            json.dump(result, f, indent=2)

        console.print(f"[green]✓[/green] Analysis complete: {out}")

        if explain:
            _run_explain(result, None, explain_out)

    except Exception as e:
        console.print(f"[red]Error during analysis:[/red] {e}")
        raise typer.Exit(code=1)


def _run_explain(analysis_data: dict, baseline_data: dict | None, out: Path) -> None:
    llm_input, llm_output, markdown = run_explain(analysis_data, baseline_data)
    json_out = out.with_suffix(".json")
    input_out = out.with_name("llm_input.json")

    import json
    with open(json_out, "w") as f:
        json.dump(llm_output, f, indent=2)
    with open(input_out, "w") as f:
        json.dump(llm_input, f, indent=2)
    with open(out, "w") as f:
        f.write(markdown)

    console.print(f"[green]✓[/green] Explanation written to: {out}")
    console.print(f"[green]✓[/green] Explanation JSON written to: {json_out}")
    console.print(f"[green]✓[/green] LLM input written to: {input_out}")


@app.command()
def explain(
    analysis: Path = typer.Option(..., "--analysis", help="Path to analysis JSON"),
    baseline: Optional[Path] = typer.Option(None, "--baseline", help="Optional baseline analysis JSON"),
    out: Path = typer.Option("explanation.md", "--out", help="Output Markdown file path")
):
    """Generate an LLM explanation from analysis JSON."""
    for path in [analysis, baseline]:
        if path is None:
            continue
        if not path.exists():
            console.print(f"[red]Error:[/red] Analysis file not found: {path}")
            raise typer.Exit(code=1)
        if not path.is_file():
            console.print(f"[red]Error:[/red] Path is not a file: {path}")
            raise typer.Exit(code=1)

    import json
    with open(analysis, "r") as f:
        analysis_data = json.load(f)
    baseline_data = None
    if baseline is not None:
        with open(baseline, "r") as f:
            baseline_data = json.load(f)

    _run_explain(analysis_data, baseline_data, out)


if __name__ == "__main__":
    app()
