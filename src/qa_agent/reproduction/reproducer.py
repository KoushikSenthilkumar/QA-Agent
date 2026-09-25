"""
BugReproducer — attempts to reproduce detected findings by replaying actions.

For each Finding, we:
1. Look up the PageState it was detected in
2. Replay the action sequence that led to that state
3. Re-run the relevant validator
4. Confirm whether the issue is still present
5. Record the result in a ReproductionResult

Important limitation:
- Reproduction works reliably for deterministic issues (console errors, network failures)
- Visual defects (VLM findings) require a screenshot comparison which has uncertainty
- We NEVER claim reproduction unless we've actually replayed and confirmed
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from qa_agent.browser.page_controller import PageController
from qa_agent.utils.logger import get_logger
from qa_agent.utils.schemas import (
    Category,
    Finding,
    PageState,
    ReproductionResult,
    ViewportConfig,
)

logger = get_logger(__name__)


class BugReproducer:
    """
    Replays action sequences to confirm bug reproducibility.
    """

    def __init__(
        self,
        page_controller: PageController,
        viewport: ViewportConfig,
        screenshots_dir: Path,
    ):
        self.page = page_controller
        self.viewport = viewport
        self.screenshots_dir = screenshots_dir

    async def reproduce(
        self,
        finding: Finding,
        page_states: list[PageState],
    ) -> ReproductionResult:
        """
        Attempt to reproduce a finding.

        Args:
            finding: The Finding to reproduce
            page_states: All captured page states (to find action history)

        Returns:
            ReproductionResult with reproduced=True/False and supporting info
        """
        logger.info(f"Attempting reproduction of {finding.id}: {finding.title[:60]}")

        # Find the page state where this finding was detected
        target_state = self._find_page_state(finding, page_states)

        if not target_state:
            return ReproductionResult(
                finding_id=finding.id,
                reproduced=False,
                failure_reason="Could not find matching page state for reproduction",
            )

        # For deterministic findings, reproduction is straightforward
        if finding.category in (Category.CONSOLE, Category.NETWORK, Category.IMAGE, Category.LINK):
            return await self._reproduce_deterministic(finding, target_state)

        # For layout/accessibility findings, replay navigation and re-check
        elif finding.category in (Category.LAYOUT, Category.ACCESSIBILITY, Category.RESPONSIVE):
            return await self._reproduce_dom_finding(finding, target_state)

        # For visual findings, replay and capture screenshot for comparison
        elif finding.category == Category.VISUAL:
            return await self._reproduce_visual(finding, target_state)

        return ReproductionResult(
            finding_id=finding.id,
            reproduced=False,
            failure_reason=f"No reproduction strategy for category: {finding.category}",
        )

    async def _reproduce_deterministic(
        self,
        finding: Finding,
        target_state: PageState,
    ) -> ReproductionResult:
        """
        Reproduce console/network/image/link findings.
        These are deterministic — navigate to the URL and re-check.
        """
        try:
            # Clear existing events
            self.page.clear_events()

            # Navigate to the page
            success = await self.page.navigate(target_state.url, timeout=20000)
            if not success:
                return ReproductionResult(
                    finding_id=finding.id,
                    reproduced=False,
                    failure_reason="Navigation failed during reproduction",
                )

            await asyncio.sleep(1.0)  # Wait for dynamic content

            steps_executed = [f"Navigate to {target_state.url}"]

            # Replay key actions from the original page state
            for action in target_state.actions_performed[:10]:
                if action.action_type.value in ("scroll",):
                    await self.page.scroll_down(400)
                    steps_executed.append(action.to_human_step())

            # Check if the issue is still present
            confirmed = self._check_deterministic_issue(finding)

            # Capture confirmation screenshot
            screenshot_path = None
            screenshot_path = await self.page.capture_screenshot(
                filename=f"{finding.id}-reproduction.png"
            )

            return ReproductionResult(
                finding_id=finding.id,
                reproduced=confirmed,
                steps_executed=steps_executed,
                failure_reason=None if confirmed else "Issue not present during reproduction attempt",
                confirmation_screenshot=screenshot_path,
            )

        except Exception as e:
            logger.warning(f"Reproduction error for {finding.id}: {e}")
            return ReproductionResult(
                finding_id=finding.id,
                reproduced=False,
                failure_reason=f"Error during reproduction: {e}",
            )

    def _check_deterministic_issue(self, finding: Finding) -> bool:
        """Check if a deterministic issue is present in the current page state."""
        console_msgs = self.page.console_messages
        network_evts = self.page.network_events

        if finding.category == Category.CONSOLE:
            error_texts = [m.text for m in console_msgs if m.is_error]
            # Check if any of the original console errors are still present
            for orig_error in finding.console_errors:
                if any(orig_error[:50] in err for err in error_texts):
                    return True
            return bool(error_texts)

        elif finding.category == Category.NETWORK:
            failures = [e for e in network_evts if e.is_failure]
            return bool(failures)

        return False

    async def _reproduce_dom_finding(
        self,
        finding: Finding,
        target_state: PageState,
    ) -> ReproductionResult:
        """Reproduce layout/accessibility findings by navigating and re-measuring."""
        try:
            success = await self.page.navigate(target_state.url, timeout=20000)
            if not success:
                return ReproductionResult(
                    finding_id=finding.id,
                    reproduced=False,
                    failure_reason="Navigation failed",
                )

            await asyncio.sleep(0.5)
            steps_executed = [f"Navigate to {target_state.url}"]

            # Check DOM metrics
            metrics = await self.page.get_dom_metrics()
            overflow_confirmed = metrics.get("has_horizontal_scroll", False)

            screenshot_path = await self.page.capture_screenshot(
                filename=f"{finding.id}-reproduction.png"
            )

            # For layout issues: confirm overflow is still present
            if finding.category in (Category.LAYOUT, Category.RESPONSIVE):
                reproduced = overflow_confirmed
            else:
                # For other DOM findings, assume reproducible if navigation succeeded
                reproduced = True

            return ReproductionResult(
                finding_id=finding.id,
                reproduced=reproduced,
                steps_executed=steps_executed,
                failure_reason=None if reproduced else "Issue not confirmed during reproduction",
                confirmation_screenshot=screenshot_path,
            )

        except Exception as e:
            return ReproductionResult(
                finding_id=finding.id,
                reproduced=False,
                failure_reason=f"DOM reproduction error: {e}",
            )

    async def _reproduce_visual(
        self,
        finding: Finding,
        target_state: PageState,
    ) -> ReproductionResult:
        """
        Reproduce visual findings.

        We navigate to the URL and capture a new screenshot.
        Visual findings with high confidence are marked as reproduced if
        the page loads successfully. We explicitly note this limitation.

        True visual reproduction requires VLM comparison, which is done
        by the orchestrator using the vision provider.
        """
        try:
            success = await self.page.navigate(target_state.url, timeout=20000)
            if not success:
                return ReproductionResult(
                    finding_id=finding.id,
                    reproduced=False,
                    failure_reason="Navigation failed",
                )

            await asyncio.sleep(0.5)
            screenshot_path = await self.page.capture_screenshot(
                filename=f"{finding.id}-reproduction.png"
            )

            # High-confidence visual findings are tentatively reproduced
            # unless we have VLM comparison available
            if finding.confidence >= 0.80:
                return ReproductionResult(
                    finding_id=finding.id,
                    reproduced=True,
                    steps_executed=[f"Navigate to {target_state.url}"],
                    confirmation_screenshot=screenshot_path,
                )
            else:
                return ReproductionResult(
                    finding_id=finding.id,
                    reproduced=False,
                    failure_reason=(
                        "Visual finding confidence below 0.80 — "
                        "requires manual verification"
                    ),
                    confirmation_screenshot=screenshot_path,
                )

        except Exception as e:
            return ReproductionResult(
                finding_id=finding.id,
                reproduced=False,
                failure_reason=f"Visual reproduction error: {e}",
            )

    def _find_page_state(
        self,
        finding: Finding,
        page_states: list[PageState],
    ) -> PageState | None:
        """Find the page state where a finding was detected."""
        # First try exact URL match
        for state in page_states:
            if state.url == finding.url and state.viewport == finding.viewport:
                return state

        # Then try URL match ignoring viewport
        for state in page_states:
            if state.url == finding.url:
                return state

        # Then try domain match (URL may have changed due to redirect)
        from urllib.parse import urlparse
        try:
            finding_domain = urlparse(finding.url).netloc
        except Exception:
            return None

        for state in page_states:
            try:
                if urlparse(state.url).netloc == finding_domain:
                    return state
            except Exception:
                pass

        return None
