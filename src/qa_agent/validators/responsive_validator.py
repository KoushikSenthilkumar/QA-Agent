"""
ResponsiveValidator — tests pages at multiple viewport sizes.

Orchestrates validation across the configured viewports and identifies
issues that only appear at specific sizes (e.g., mobile overflow, tablet layout breaks).

Note: The actual validation at each viewport is done by other validators.
This validator compares results across viewports to find viewport-specific issues
and synthesizes responsive-specific findings.
"""

from __future__ import annotations

from qa_agent.utils.schemas import Category, Finding, PageState, Severity
from qa_agent.validators.base import BaseValidator


class ResponsiveValidator(BaseValidator):
    """
    Compares page states across multiple viewports to detect responsive failures.

    This validator is called with results from multiple viewport scans.
    It identifies issues that are viewport-specific.
    """

    async def validate(self, page_state: PageState, **kwargs) -> list[Finding]:
        """
        Validates responsive behavior at a single viewport.
        Cross-viewport comparison is done in compare_viewports().
        """
        findings = []
        dom_metrics = page_state.dom_metrics

        if not dom_metrics:
            return []

        # Parse viewport dimensions
        try:
            width_str, height_str = page_state.viewport.split("x")
            viewport_width = int(width_str)
        except (ValueError, AttributeError):
            return []

        # On mobile/tablet, horizontal overflow is especially bad
        if viewport_width <= 768:
            has_overflow = dom_metrics.get("has_horizontal_scroll", False)
            if has_overflow:
                scroll_width = dom_metrics.get("scroll_width", 0)
                overflow = scroll_width - viewport_width
                findings.append(
                    Finding(
                        title=f"Mobile/tablet horizontal overflow ({overflow}px) on {page_state.viewport}",
                        severity=Severity.HIGH,
                        category=Category.RESPONSIVE,
                        url=page_state.url,
                        viewport=page_state.viewport,
                        description=(
                            f"The page has horizontal overflow on a {page_state.viewport} viewport. "
                            f"This is a significant usability issue on mobile devices where horizontal "
                            f"scrolling is unexpected and disrupts the reading experience.\n\n"
                            f"Content extends {overflow}px beyond the viewport edge."
                        ),
                        steps_to_reproduce=[
                            f"Open {page_state.url} on a device or emulator at {page_state.viewport}",
                            "Observe horizontal scrollbar or attempt to scroll right",
                        ],
                        expected=f"No horizontal overflow at {page_state.viewport} viewport",
                        actual=f"Content overflows by {overflow}px on {page_state.viewport}",
                        confidence=1.0,
                        screenshot=page_state.screenshot_path,
                    )
                )

        return findings

    @staticmethod
    def compare_viewports(
        states_by_viewport: dict[str, PageState],
        url: str,
    ) -> list[Finding]:
        """
        Compare page states across viewports to find viewport-specific regressions.
        Called by the orchestrator after all viewports are tested.
        """
        findings = []

        # Find pages that have overflow at mobile but not desktop
        mobile_states = [
            (vp, state) for vp, state in states_by_viewport.items()
            if "390" in vp or "768" in vp
        ]
        desktop_states = [
            (vp, state) for vp, state in states_by_viewport.items()
            if "1366" in vp or "1280" in vp
        ]

        for mobile_vp, mobile_state in mobile_states:
            if not mobile_state.dom_metrics.get("has_horizontal_scroll", False):
                continue
            # Check if desktop is clean
            desktop_ok = all(
                not d_state.dom_metrics.get("has_horizontal_scroll", False)
                for _, d_state in desktop_states
            )
            if desktop_ok:
                findings.append(
                    Finding(
                        title=f"Responsive layout failure: overflow at {mobile_vp} only",
                        severity=Severity.HIGH,
                        category=Category.RESPONSIVE,
                        url=url,
                        viewport=mobile_vp,
                        description=(
                            f"The page has horizontal overflow at {mobile_vp} but renders correctly "
                            f"at desktop viewport sizes. This is a classic responsive design failure — "
                            f"the layout does not adapt properly for smaller screens."
                        ),
                        steps_to_reproduce=[
                            f"Open {url}",
                            f"Resize browser to {mobile_vp}",
                            "Observe horizontal overflow that is absent at desktop size",
                        ],
                        expected="Page layout should be responsive and work at all viewport sizes",
                        actual=f"Horizontal overflow present at {mobile_vp} but not at desktop",
                        confidence=1.0,
                        screenshot=mobile_state.screenshot_path,
                    )
                )

        return findings
