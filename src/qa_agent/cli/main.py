"""
CLI entry point for the QA Agent.

Usage:
    qa-agent https://example.com
    qa-agent https://example.com --max-pages 10 --headless false
    qa-agent baseline https://example.com
    qa-agent compare https://example.com
    qa-agent --config myconfig.yaml https://example.com
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import click
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import box

from qa_agent.utils.config import load_config
from qa_agent.utils.logger import setup_logging
from qa_agent.utils.schemas import QARunResult

console = Console()

# Load .env file if present
load_dotenv()


def _print_banner() -> None:
    console.print(
        Panel.fit(
            "[bold blue]AUTONOMOUS MULTIMODAL QA AGENT[/bold blue]\n"
            "[dim]Deterministic + AI-powered web application testing[/dim]",
            border_style="blue",
        )
    )


def _print_result(result: QARunResult) -> None:
    """Print a clean summary of the QA run results."""
    console.print()

    # Exploration stats
    t = Table(show_header=False, box=box.SIMPLE, padding=(0, 2))
    t.add_column("Key", style="dim")
    t.add_column("Value", style="bold")
    t.add_row("Target", result.target_url)
    t.add_row("Run ID", result.run_id)
    t.add_row("Duration", f"{result.duration_seconds:.1f}s" if result.duration_seconds else "N/A")
    t.add_row("Pages discovered", str(result.pages_discovered))
    t.add_row("States tested", str(result.states_tested))
    t.add_row("Actions performed", str(result.actions_performed))
    t.add_row("Viewports tested", str(result.viewports_tested))
    console.print(Panel(t, title="[bold]Exploration[/bold]", border_style="dim"))

    # Validators
    validator_text = Text()
    for v in result.validators_run:
        if v in result.validators_failed:
            validator_text.append(f"  ✗ {v}\n", style="red")
        else:
            validator_text.append(f"  ✓ {v}\n", style="green")
    if validator_text:
        console.print(Panel(validator_text, title="[bold]Validators[/bold]", border_style="dim"))

    # Results
    results_table = Table(show_header=False, box=box.SIMPLE, padding=(0, 2))
    results_table.add_column("Severity", style="bold")
    results_table.add_column("Count", justify="right")
    if result.critical_count > 0:
        results_table.add_row("[red]Critical[/red]", str(result.critical_count))
    if result.high_count > 0:
        results_table.add_row("[orange1]High[/orange1]", str(result.high_count))
    if result.medium_count > 0:
        results_table.add_row("[yellow]Medium[/yellow]", str(result.medium_count))
    if result.low_count > 0:
        results_table.add_row("[blue]Low[/blue]", str(result.low_count))
    if result.info_count > 0:
        results_table.add_row("[dim]Info[/dim]", str(result.info_count))
    if result.total_issues == 0:
        results_table.add_row("[green]No issues detected[/green]", "")
    console.print(Panel(results_table, title="[bold]Results[/bold]", border_style="dim"))

    # Reproduction
    total_attempted = len(result.reproduction_results)
    if total_attempted > 0:
        repro_text = Text()
        repro_text.append(f"  Reproduced:           {result.reproduced_count}\n", style="green")
        repro_text.append(
            f"  Could not reproduce:  {total_attempted - result.reproduced_count}\n",
            style="dim",
        )
        console.print(Panel(repro_text, title="[bold]Reproduction[/bold]", border_style="dim"))

    # Evidence
    evidence_text = Text()
    evidence_text.append(f"  Screenshots:  {result.screenshots_dir}\n", style="dim")
    evidence_text.append(f"  Recordings:   {result.recordings_dir}\n", style="dim")
    evidence_text.append(f"  Logs:         {result.logs_dir}\n", style="dim")
    console.print(Panel(evidence_text, title="[bold]Evidence[/bold]", border_style="dim"))

    # Report paths & Perfection Score
    report_text = Text()
    report_text.append(f"  Perfection Score: {result.perfection_score}% ({result.perfection_rating})\n", style="bold green" if result.perfection_score >= 80 else "bold yellow")
    if result.report_html_path:
        report_text.append(f"  HTML:  {result.report_html_path}\n", style="cyan bold")
    if result.report_excel_path:
        report_text.append(f"  Excel: {result.report_excel_path}\n", style="green bold")
    if result.report_docx_path:
        report_text.append(f"  Word:  {result.report_docx_path}\n", style="blue bold")
    if result.report_json_path:
        report_text.append(f"  JSON:  {result.report_json_path}\n", style="cyan")
    if report_text:
        console.print(Panel(report_text, title="[bold]Reports & Score[/bold]", border_style="cyan"))

    # Final verdict
    if result.critical_count > 0:
        console.print("\n[bold red]⚠ CRITICAL issues detected — immediate attention required[/bold red]")
    elif result.high_count > 0:
        console.print("\n[bold orange1]⚑ HIGH severity issues detected[/bold orange1]")
    elif result.total_issues == 0:
        console.print("\n[bold green]✓ QA PASSED — No issues detected[/bold green]")
    else:
        console.print(f"\n[bold yellow]⚠ QA complete — {result.total_issues} issue(s) found[/bold yellow]")


async def _run_orchestrator(config):
    from qa_agent.agent.orchestrator import QAOrchestrator
    orchestrator = QAOrchestrator(config)
    return await orchestrator.run()


def _build_config(
    url: str,
    config_path: str | None,
    max_pages: int | None,
    max_actions: int | None,
    max_depth: int | None,
    timeout: int | None,
    headless: bool | None,
    provider: str | None,
    desktop_only: bool,
    no_video: bool,
    output_dir: str | None,
):
    """Build QAConfig from CLI arguments."""
    overrides: dict = {}
    exploration_overrides: dict = {}

    if max_pages:
        exploration_overrides["max_pages"] = max_pages
    if max_actions:
        exploration_overrides["max_actions"] = max_actions
    if max_depth:
        exploration_overrides["max_depth"] = max_depth
    if timeout:
        exploration_overrides["timeout"] = timeout
    if exploration_overrides:
        overrides["exploration"] = exploration_overrides

    if headless is not None:
        overrides["headless"] = headless
    if provider:
        overrides["vision"] = {"provider": provider}
    if no_video:
        overrides["recording"] = {"enabled": False}
    if output_dir:
        overrides["output"] = {
            "reports_dir": f"{output_dir}/reports",
            "screenshots_dir": f"{output_dir}/screenshots",
            "recordings_dir": f"{output_dir}/recordings",
            "logs_dir": f"{output_dir}/logs",
        }

    config = load_config(config_path=config_path, overrides=overrides)

    # Normalize and set URL
    if url and not url.startswith(("http://", "https://")):
        url = "https://" + url
    config.target_url = url

    if desktop_only:
        config.viewports = config.viewports[:1]

    return config


# ─────────────────────────────────────────────────────────────────────────────
# Main CLI group
# ─────────────────────────────────────────────────────────────────────────────

@click.group()
def cli():
    """
    Autonomous Multimodal QA Agent — test websites autonomously.

    \b
    Commands:
        run       Run QA analysis on a URL (default)
        baseline  Store visual baselines
        compare   Run QA with visual regression comparison
    
    \b
    Quick start:
        qa-agent run https://example.com
        qa-agent run https://example.com --provider openai
    """
    pass


@cli.command("run")
@click.argument("url")
@click.option("--config", "-c", "config_path", default=None, help="Path to config YAML file")
@click.option("--max-pages", default=None, type=int, help="Maximum pages to discover")
@click.option("--max-actions", default=None, type=int, help="Maximum total actions")
@click.option("--max-depth", default=None, type=int, help="Maximum exploration depth")
@click.option("--timeout", default=None, type=int, help="Run timeout in seconds")
@click.option("--headless/--no-headless", default=None, help="Run browser in headless mode")
@click.option(
    "--provider",
    default=None,
    type=click.Choice(["openai", "mock"], case_sensitive=False),
    help="VLM provider (openai or mock)",
)
@click.option("--desktop-only", is_flag=True, default=False, help="Only test desktop viewport")
@click.option("--no-video", is_flag=True, default=False, help="Disable video recording")
@click.option("--log-level", default="INFO", help="Logging verbosity (DEBUG/INFO/WARNING)")
@click.option("--output-dir", default=None, help="Custom output directory")
def run_command(
    url,
    config_path,
    max_pages,
    max_actions,
    max_depth,
    timeout,
    headless,
    provider,
    desktop_only,
    no_video,
    log_level,
    output_dir,
):
    """Run autonomous QA analysis on a URL.

    \b
    Examples:
        qa-agent run https://example.com
        qa-agent run https://example.com --max-pages 10 --provider openai
        qa-agent run https://example.com --desktop-only --no-headless
    """
    _print_banner()
    setup_logging(log_level=log_level)

    config = _build_config(
        url=url,
        config_path=config_path,
        max_pages=max_pages,
        max_actions=max_actions,
        max_depth=max_depth,
        timeout=timeout,
        headless=headless,
        provider=provider,
        desktop_only=desktop_only,
        no_video=no_video,
        output_dir=output_dir,
    )

    console.print(f"\n[bold]Target:[/bold] {url}")
    console.print(f"[dim]Viewports: {len(config.viewports)} | "
                  f"Provider: {config.vision.provider} | "
                  f"Max pages: {config.exploration.max_pages}[/dim]\n")

    try:
        result = asyncio.run(_run_orchestrator(config))
        _print_result(result)
        if result.critical_count > 0:
            sys.exit(2)
        elif result.high_count > 0:
            sys.exit(1)
    except KeyboardInterrupt:
        console.print("\n[yellow]QA run interrupted by user[/yellow]")
        sys.exit(130)
    except Exception as e:
        console.print(f"\n[red]QA run failed: {e}[/red]")
        if log_level == "DEBUG":
            import traceback
            traceback.print_exc()
        sys.exit(1)


@cli.command("baseline")
@click.argument("url")
@click.option("--config", "-c", "config_path", default=None)
@click.option("--log-level", default="INFO")
def baseline(url: str, config_path: str | None, log_level: str):
    """Store visual baselines for a URL.

    \b
    Example:
        qa-agent baseline https://example.com
    """
    _print_banner()
    setup_logging(log_level=log_level)
    console.print(f"\n[bold]Storing baselines for:[/bold] {url}\n")

    if url and not url.startswith(("http://", "https://")):
        url = "https://" + url

    config = load_config(config_path=config_path)
    config.target_url = url

    async def _run():
        from qa_agent.reporting.baseline import BaselineManager
        from qa_agent.browser.manager import BrowserManager
        from qa_agent.browser.page_controller import PageController
        import asyncio

        baselines_dir = Path(config.output.baselines_dir)
        bm = BaselineManager(baselines_dir)
        screenshots_dir = Path(config.output.screenshots_dir) / "baselines"
        screenshots_dir.mkdir(parents=True, exist_ok=True)

        async with BrowserManager(config=config) as browser:
            for viewport in config.viewports:
                page, context = await browser.new_page(viewport=viewport)
                pc = PageController(page=page, screenshots_dir=screenshots_dir)
                pc.start_monitoring()

                await pc.navigate(url)
                await asyncio.sleep(1.0)

                screenshot_path = await pc.capture_screenshot(
                    filename=f"baseline_temp_{str(viewport)}.png"
                )
                if screenshot_path:
                    stored = await bm.store_baseline(screenshot_path, url, str(viewport))
                    console.print(f"[green]✓[/green] Baseline stored for {viewport.label}: {stored}")
                else:
                    console.print(f"[red]✗[/red] Failed to capture baseline for {viewport.label}")

                pc.stop_monitoring()
                await context.close()

    try:
        asyncio.run(_run())
        console.print("\n[green]Baselines stored successfully.[/green]")
    except Exception as e:
        console.print(f"\n[red]Baseline failed: {e}[/red]")
        sys.exit(1)


@cli.command("compare")
@click.argument("url")
@click.option("--config", "-c", "config_path", default=None)
@click.option("--log-level", default="INFO")
@click.option("--max-pages", default=None, type=int)
@click.option("--provider", default=None, type=click.Choice(["openai", "mock"], case_sensitive=False))
def compare_command(url: str, config_path: str | None, log_level: str, max_pages, provider):
    """Run QA with visual regression comparison against stored baselines.

    \b
    Example:
        qa-agent compare https://example.com
    """
    _print_banner()
    setup_logging(log_level=log_level)
    console.print(f"\n[bold]Running visual regression comparison for:[/bold] {url}\n")

    if url and not url.startswith(("http://", "https://")):
        url = "https://" + url

    config = _build_config(
        url=url,
        config_path=config_path,
        max_pages=max_pages,
        max_actions=None,
        max_depth=None,
        timeout=None,
        headless=None,
        provider=provider,
        desktop_only=False,
        no_video=False,
        output_dir=None,
    )

    async def _run():
        from qa_agent.agent.orchestrator import QAOrchestrator
        from qa_agent.reporting.baseline import BaselineManager

        orchestrator = QAOrchestrator(config)
        result = await orchestrator.run()

        baselines_dir = Path(config.output.baselines_dir)
        bm = BaselineManager(baselines_dir)

        for viewport in config.viewports:
            screenshots_dir = Path(result.screenshots_dir)
            screenshots = list(screenshots_dir.glob("*.png"))
            for screenshot in screenshots[:3]:
                regression_finding = await bm.compare(
                    current_path=str(screenshot),
                    url=url,
                    viewport=str(viewport),
                    diff_dir=screenshots_dir / "diffs",
                )
                if regression_finding:
                    result.findings.append(regression_finding)
                    console.print(f"[red]Visual regression detected at {viewport.label}[/red]")

        return result

    try:
        result = asyncio.run(_run())
        _print_result(result)
    except Exception as e:
        console.print(f"\n[red]Compare failed: {e}[/red]")
        sys.exit(1)


# Allow direct invocation with URL as first arg (convenience shorthand)
# qa-agent https://example.com  →  equivalent to  qa-agent run https://example.com
@cli.result_callback()
def process_result(result, **kwargs):
    pass


def main():
    """Entry point that supports both 'qa-agent run URL' and 'qa-agent URL' syntax."""
    import sys as _sys
    args = _sys.argv[1:]
    
    # If first arg looks like a URL (starts with http/https or contains .), 
    # and is not a known subcommand, prepend 'run'
    known_commands = {'run', 'baseline', 'compare', '--help', '-h', '--version'}
    if args and args[0] not in known_commands and not args[0].startswith('-'):
        _sys.argv.insert(1, 'run')
    
    cli()


if __name__ == "__main__":
    main()
