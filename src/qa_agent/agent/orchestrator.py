"""
QAOrchestrator — drives the complete QA run.

This is the top-level coordinator that:
1. Initializes all subsystems
2. Creates the browser and page context
3. Runs the exploration engine
4. Runs all validators on each discovered state
5. Deduplicates findings
6. Attempts bug reproduction
7. Generates evidence artifacts
8. Produces the final report

The orchestrator is driven by QAConfig and returns a QARunResult.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from pathlib import Path

from qa_agent.browser.manager import BrowserManager
from qa_agent.browser.page_controller import PageController
from qa_agent.crawler.explorer import ExplorationEngine
from qa_agent.evidence.annotator import ScreenshotAnnotator
from qa_agent.evidence.collector import EvidenceCollector
from qa_agent.reproduction.reproducer import BugReproducer
from qa_agent.reporting.generator import ReportGenerator
from qa_agent.utils.config import QAConfig
from qa_agent.utils.logger import get_logger
from qa_agent.utils.schemas import (
    Finding,
    PageState,
    QARunResult,
    Severity,
)
from qa_agent.validators import (
    AccessibilityValidator,
    ConsoleValidator,
    ImageValidator,
    LayoutValidator,
    LinkValidator,
    NetworkValidator,
    ResponsiveValidator,
    VisualValidator,
)
from qa_agent.vision.factory import create_vision_provider

logger = get_logger(__name__)


class QAOrchestrator:
    """
    Coordinates the complete QA validation run.

    Usage:
        config = load_config()
        config.target_url = "https://example.com"
        orchestrator = QAOrchestrator(config)
        result = await orchestrator.run()
    """

    def __init__(self, config: QAConfig):
        self.config = config
        self.result = QARunResult(target_url=config.target_url)

        # Resolve output directories for this run
        self._dirs = self._setup_dirs()

    def _setup_dirs(self) -> dict[str, Path]:
        run_id = self.result.run_id
        dirs = {
            "reports": Path(self.config.output.reports_dir) / run_id,
            "screenshots": Path(self.config.output.screenshots_dir) / run_id,
            "recordings": Path(self.config.output.recordings_dir) / run_id,
            "logs": Path(self.config.output.logs_dir) / run_id,
        }
        for d in dirs.values():
            d.mkdir(parents=True, exist_ok=True)
        return dirs

    async def run(self) -> QARunResult:
        """Execute the complete QA run."""
        start_time = time.time()
        logger.info(f"Starting QA run {self.result.run_id} for {self.config.target_url}")

        self.result.started_at = datetime.now(timezone.utc).isoformat()

        # Initialize shared components
        vision_provider = create_vision_provider(self.config)
        evidence = EvidenceCollector(
            screenshots_dir=self._dirs["screenshots"],
            logs_dir=self._dirs["logs"],
            recordings_dir=self._dirs["recordings"],
        )
        annotator = ScreenshotAnnotator(output_dir=self._dirs["screenshots"])
        all_page_states: list[PageState] = []
        all_findings: list[Finding] = []

        # Initialize validators
        validators_map = {
            "console": ConsoleValidator() if self.config.validators.console else None,
            "network": NetworkValidator() if self.config.validators.network else None,
            "images": ImageValidator() if self.config.validators.images else None,
            "links": LinkValidator() if self.config.validators.links else None,
            "layout": LayoutValidator() if self.config.validators.layout else None,
            "accessibility": AccessibilityValidator() if self.config.validators.accessibility else None,
            "responsive": ResponsiveValidator() if self.config.validators.responsive else None,
            "visual_ai": VisualValidator(vision_provider) if self.config.validators.visual_ai else None,
        }
        self.result.validators_run = [k for k, v in validators_map.items() if v is not None]

        # ─────────────────────────────────────────────────────────────────────
        # Run exploration and validation per viewport
        # ─────────────────────────────────────────────────────────────────────
        viewports = self.config.viewports
        states_by_viewport: dict[str, list[PageState]] = {}

        async with BrowserManager(
            config=self.config,
            recordings_dir=self._dirs["recordings"],
        ) as browser_manager:
            for viewport in viewports:
                logger.info(f"Testing viewport: {viewport.label}")

                page, context = await browser_manager.new_page(
                    viewport=viewport,
                    record_video=self.config.recording.enabled,
                )

                page_controller = PageController(
                    page=page,
                    screenshots_dir=self._dirs["screenshots"],
                )
                page_controller.start_monitoring()

                # Exploration
                explorer = ExplorationEngine(
                    config=self.config,
                    vision_provider=vision_provider,
                    page_controller=page_controller,
                    viewport=viewport,
                )

                try:
                    page_states = await asyncio.wait_for(
                        explorer.explore(),
                        timeout=self.config.exploration.timeout,
                    )
                except asyncio.TimeoutError:
                    logger.warning(f"Exploration timed out for {viewport.label}")
                    page_states = explorer.state.page_states

                states_by_viewport[str(viewport)] = page_states
                all_page_states.extend(page_states)
                self.result.states_tested += len(page_states)
                self.result.actions_performed += explorer.state.total_actions

                # Run validators on each page state
                for ps in page_states:
                    state_findings = await self._validate_page_state(
                        ps, validators_map, page_controller
                    )
                    all_findings.extend(state_findings)

                # Close context (finalizes video recording)
                page_controller.stop_monitoring()
                await context.close()
                self.result.viewports_tested += 1

                # Save collected logs after each viewport
                await evidence.save_console_log(
                    [m for ps in page_states for m in ps.console_messages],
                    run_id=f"{self.result.run_id}_{str(viewport)}",
                )
                await evidence.save_network_log(
                    [e for ps in page_states for e in ps.network_events],
                    run_id=f"{self.result.run_id}_{str(viewport)}",
                )

            # Responsive cross-viewport comparison
            if self.config.validators.responsive and len(states_by_viewport) > 1:
                logger.info("Running cross-viewport responsive comparison")
                for url in self._get_tested_urls(all_page_states):
                    url_states = {
                        vp: next((ps for ps in states if ps.url == url), None)
                        for vp, states in states_by_viewport.items()
                    }
                    url_states_clean = {k: v for k, v in url_states.items() if v}
                    if len(url_states_clean) > 1:
                        responsive_findings = ResponsiveValidator.compare_viewports(
                            url_states_clean, url
                        )
                        all_findings.extend(responsive_findings)

        # ─────────────────────────────────────────────────────────────────────
        # Post-processing
        # ─────────────────────────────────────────────────────────────────────

        # Deduplicate findings
        all_findings = self._deduplicate(all_findings)

        # Assign sequential bug IDs
        all_findings = self._assign_bug_ids(all_findings)

        # Annotate screenshots
        for finding in all_findings:
            if finding.screenshot and Path(finding.screenshot).exists():
                annotated = await annotator.annotate(
                    source_path=finding.screenshot,
                    finding=finding,
                )
                if annotated:
                    finding.annotated_screenshot = annotated

        # Bug reproduction (run on primary viewport)
        if all_findings and self.config.viewports:
            await self._attempt_reproductions(
                all_findings,
                all_page_states,
                browser_manager if False else None,  # Browser already closed — use new session
            )

        # Finalize result
        self.result.findings = all_findings
        self.result.pages_discovered = len(set(ps.url for ps in all_page_states))
        self.result.completed_at = datetime.now(timezone.utc).isoformat()
        self.result.duration_seconds = time.time() - start_time

        # Save evidence and generate reports
        await evidence.save_page_states(all_page_states, self.result.run_id)
        self.result.screenshots_dir = str(self._dirs["screenshots"])
        self.result.recordings_dir = str(self._dirs["recordings"])
        self.result.logs_dir = str(self._dirs["logs"])

        report_gen = ReportGenerator(reports_dir=self._dirs["reports"])
        if "json" in self.config.output.report_formats:
            self.result.report_json_path = await report_gen.generate_json(self.result)
        if "html" in self.config.output.report_formats:
            self.result.report_html_path = await report_gen.generate_html(self.result)
        
        # Automatically generate Excel (.xlsx) and Word (.docx) reports
        self.result.report_excel_path = await report_gen.generate_excel(self.result)
        self.result.report_docx_path = await report_gen.generate_docx(self.result)

        logger.info(
            f"QA run complete: {self.result.total_issues} issues, "
            f"{self.result.duration_seconds:.1f}s"
        )
        return self.result

    async def _validate_page_state(
        self,
        page_state: PageState,
        validators_map: dict,
        page_controller: PageController,
    ) -> list[Finding]:
        """Run all applicable validators on a single page state."""
        findings = []

        # Collect additional data needed by validators
        extra_data: dict = {}

        # Get images and links from the page (only if validators need them)
        if validators_map.get("images") or validators_map.get("links"):
            try:
                extra_data["images"] = await page_controller.get_images()
                extra_data["links"] = await page_controller.get_links()
            except Exception:
                pass

        if validators_map.get("layout"):
            try:
                extra_data["overflow_elements"] = await page_controller.get_overflow_elements()
            except Exception:
                pass

        if validators_map.get("accessibility") and self.config.accessibility.run_axe:
            try:
                extra_data["axe_results"] = await page_controller.run_axe(
                    tags=self.config.accessibility.axe_tags
                )
            except Exception:
                pass

        if validators_map.get("visual_ai"):
            try:
                extra_data["screenshot_b64"] = await page_controller.capture_screenshot_b64()
            except Exception:
                pass

        # Run validators
        for name, validator in validators_map.items():
            if validator is None:
                continue
            try:
                vf = await validator.safe_validate(page_state, **extra_data)
                if vf:
                    findings.extend(vf)
                    logger.debug(f"{name}: {len(vf)} finding(s) on {page_state.url}")
            except Exception as e:
                logger.error(f"Validator {name} crashed: {e}", exc_info=True)
                self.result.validators_failed.append(name)

        return findings

    def _deduplicate(self, findings: list[Finding]) -> list[Finding]:
        """
        Remove duplicate findings using content-based deduplication.

        Two findings are considered duplicates if they have:
        - Same category
        - Same URL
        - Very similar title (first 60 chars match)
        """
        seen: set[str] = set()
        unique: list[Finding] = []

        for f in findings:
            key = f"{f.category.value}|{f.url}|{f.title[:60]}"
            if key not in seen:
                seen.add(key)
                unique.append(f)
            else:
                logger.debug(f"Deduplicated finding: {f.title[:60]}")

        deduped_count = len(findings) - len(unique)
        if deduped_count:
            logger.info(f"Deduplicated {deduped_count} duplicate finding(s)")
        return unique

    def _assign_bug_ids(self, findings: list[Finding]) -> list[Finding]:
        """Assign sequential BUG-NNN IDs sorted by severity."""
        sorted_findings = sorted(findings, key=lambda f: f.severity.order)
        for i, finding in enumerate(sorted_findings, start=1):
            finding.id = f"BUG-{i:03d}"
        return sorted_findings

    def _get_tested_urls(self, page_states: list[PageState]) -> list[str]:
        """Get unique URLs from all page states."""
        return list(set(ps.url for ps in page_states))

    async def _attempt_reproductions(
        self,
        findings: list[Finding],
        page_states: list[PageState],
        browser_manager,  # May be None if browser was closed
    ) -> None:
        """
        Attempt to reproduce findings using a fresh browser session.
        Only attempts reproduction for HIGH/CRITICAL/MEDIUM findings.
        """
        reproducible_findings = [
            f for f in findings
            if f.severity in (Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM)
        ]

        if not reproducible_findings:
            return

        logger.info(f"Attempting reproduction of {len(reproducible_findings)} findings")

        primary_viewport = self.config.viewports[0]

        try:
            async with BrowserManager(config=self.config) as bm:
                page, context = await bm.new_page(viewport=primary_viewport)
                pc = PageController(page=page, screenshots_dir=self._dirs["screenshots"])
                pc.start_monitoring()

                reproducer = BugReproducer(
                    page_controller=pc,
                    viewport=primary_viewport,
                    screenshots_dir=self._dirs["screenshots"],
                )

                for finding in reproducible_findings:
                    try:
                        repro_result = await asyncio.wait_for(
                            reproducer.reproduce(finding, page_states),
                            timeout=30.0,
                        )
                        self.result.reproduction_results.append(repro_result)

                        # Update finding with reproduction status
                        finding.reproduced = repro_result.reproduced
                        if repro_result.confirmation_screenshot:
                            finding.raw_evidence["reproduction_screenshot"] = (
                                repro_result.confirmation_screenshot
                            )

                        status = "✓ Reproduced" if repro_result.reproduced else "✗ Not reproduced"
                        logger.info(f"{finding.id}: {status}")

                    except asyncio.TimeoutError:
                        logger.warning(f"Reproduction timed out for {finding.id}")
                    except Exception as e:
                        logger.warning(f"Reproduction error for {finding.id}: {e}")

                pc.stop_monitoring()
                await context.close()

        except Exception as e:
            logger.error(f"Reproduction session failed: {e}")
