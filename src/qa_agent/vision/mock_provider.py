"""
MockVisionProvider — deterministic fake VLM for testing.

This is not a stub — it is a proper implementation that:
1. Returns realistic structured responses
2. Allows the entire pipeline to run without API credits
3. Can be configured to simulate specific findings (for unit tests)
4. Cycles through a varied set of responses to avoid detection of mock mode

Design principle: The mock should exercise the full pipeline exactly as the
real provider does. If the pipeline breaks with mock, it would also break live.
"""

from __future__ import annotations

import itertools
from typing import Any

from qa_agent.utils.logger import get_logger
from qa_agent.vision.base import (
    ActionPlan,
    VisualAnalysisResult,
    VisualFinding,
    VisionProvider,
)

logger = get_logger(__name__)

# A set of varied mock visual findings the mock cycles through
_MOCK_FINDINGS_POOL = [
    VisualFinding(
        type="visual_defect",
        severity="INFO",
        title="No significant visual issues detected",
        description="The page appears to render correctly at this viewport. No obvious layout defects, overlapping elements, or text clipping were observed.",
        location="full page",
        confidence=0.75,
    ),
    VisualFinding(
        type="visual_defect",
        severity="LOW",
        title="Potential spacing inconsistency",
        description="Margin between navigation items appears slightly uneven compared to expected design conventions. This is a minor aesthetic issue.",
        location="navigation bar",
        confidence=0.60,
    ),
    VisualFinding(
        type="visual_defect",
        severity="MEDIUM",
        title="Text appears to be clipped at viewport edge",
        description="Some text content near the right edge of the viewport may be partially clipped, particularly on narrower viewport sizes.",
        location="main content area",
        confidence=0.70,
    ),
    VisualFinding(
        type="visual_defect",
        severity="INFO",
        title="Page renders adequately at this viewport",
        description="Layout structure is intact. Navigation, content, and footer are visible and correctly positioned.",
        location="full page",
        confidence=0.80,
    ),
]

_MOCK_ACTION_POOL = [
    ActionPlan(
        action="scroll",
        target_description="page body",
        value="down",
        reason="Exploring page content below the fold",
        confidence=0.85,
    ),
    ActionPlan(
        action="click",
        target_description="primary navigation link",
        reason="Discovering linked pages to expand test coverage",
        confidence=0.80,
    ),
    ActionPlan(
        action="scroll",
        target_description="page",
        value="up",
        reason="Returning to page top for full observation",
        confidence=0.75,
    ),
    ActionPlan(
        action="done",
        target_description="",
        reason="Page has been sufficiently explored at this depth",
        confidence=0.90,
        exploration_complete=True,
    ),
]


class MockVisionProvider(VisionProvider):
    """
    Mock VLM provider for testing without API credits.

    Returns deterministic but varied responses so the full pipeline
    can be exercised in tests and CI.
    """

    def __init__(self, always_find_issues: bool = False, always_clean: bool = False):
        """
        Args:
            always_find_issues: If True, always return a MEDIUM finding.
            always_clean: If True, always return INFO/no issues.
        """
        self.always_find_issues = always_find_issues
        self.always_clean = always_clean
        self._analysis_cycle = itertools.cycle(_MOCK_FINDINGS_POOL)
        self._action_cycle = itertools.cycle(_MOCK_ACTION_POOL)
        self._call_count = 0
        logger.info("Using MockVisionProvider — no VLM API calls will be made")

    async def analyze_screenshot(
        self,
        screenshot_b64: str,
        context: dict[str, Any],
    ) -> VisualAnalysisResult:
        self._call_count += 1
        url = context.get("url", "unknown")
        viewport = context.get("viewport", "unknown")

        if self.always_clean:
            finding = _MOCK_FINDINGS_POOL[0]
        elif self.always_find_issues:
            finding = _MOCK_FINDINGS_POOL[2]
        else:
            finding = next(self._analysis_cycle)

        logger.debug(f"[MockVLM] analyze_screenshot #{self._call_count} — {url} @ {viewport}")
        return VisualAnalysisResult(
            findings=[finding],
            overall_assessment=f"Mock assessment for {url} at {viewport}. No AI analysis performed.",
            page_summary=f"Page: {context.get('title', 'Unknown')}. URL: {url}.",
            raw_response="[MOCK RESPONSE]",
            provider="mock",
            model="mock-v1",
            analysis_successful=True,
        )

    async def plan_action(
        self,
        screenshot_b64: str,
        page_context: dict[str, Any],
    ) -> ActionPlan:
        self._call_count += 1
        actions_taken = page_context.get("actions_taken", 0)

        # After enough actions, signal completion
        if actions_taken > 5:
            return ActionPlan(
                action="done",
                target_description="",
                reason="Mock exploration limit reached",
                confidence=0.90,
                exploration_complete=True,
            )

        plan = next(self._action_cycle)
        logger.debug(f"[MockVLM] plan_action #{self._call_count} → {plan.action}")
        return plan

    async def describe_bug(
        self,
        screenshot_b64: str,
        finding_context: dict[str, Any],
    ) -> str:
        return (
            f"[Mock description] A {finding_context.get('category', 'unknown')} issue was detected "
            f"at {finding_context.get('url', 'unknown URL')}. "
            f"This description was generated by the MockVisionProvider."
        )
