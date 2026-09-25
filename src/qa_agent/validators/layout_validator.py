"""
LayoutValidator — detects DOM layout problems using browser measurements.

Uses JavaScript DOM API measurements collected by PageController.
This is fully deterministic.

Detects:
- Horizontal overflow (content wider than viewport)
- Elements clipped outside the visible area
- Invisible interactive elements (zero dimensions)
- Suspiciously small interactive elements (accessibility concern)

Severity rules:
- Horizontal overflow → MEDIUM (HIGH if severe)
- Interactive elements invisible → HIGH
- Interactive elements too small → LOW
"""

from __future__ import annotations

from qa_agent.utils.schemas import Category, Finding, PageState, Severity
from qa_agent.validators.base import BaseValidator

# Minimum sizes for interactive elements (accessibility guideline: 44x44px)
_MIN_INTERACTIVE_SIZE = 20  # px — below this is almost certainly a bug
_RECOMMENDED_MIN_SIZE = 44  # px — WCAG 2.5.5 target size


class LayoutValidator(BaseValidator):
    """
    Validates page layout integrity using DOM measurements.
    """

    async def validate(self, page_state: PageState, **kwargs) -> list[Finding]:
        findings = []
        dom_metrics = page_state.dom_metrics
        overflow_elements: list[dict] = kwargs.get("overflow_elements", [])

        if dom_metrics:
            findings.extend(self._check_overflow(page_state, dom_metrics))

        if overflow_elements:
            findings.extend(self._check_overflow_elements(page_state, overflow_elements))

        # Check visible elements for layout issues
        findings.extend(self._check_interactive_elements(page_state))

        self.logger.info(f"LayoutValidator: {len(findings)} finding(s) on {page_state.url}")
        return findings

    def _check_overflow(self, page_state: PageState, metrics: dict) -> list[Finding]:
        findings = []

        has_horizontal_scroll = metrics.get("has_horizontal_scroll", False)
        scroll_width = metrics.get("scroll_width", 0)
        client_width = metrics.get("client_width", 0)

        if has_horizontal_scroll and scroll_width > client_width + 20:
            overflow_amount = scroll_width - client_width
            severity = Severity.HIGH if overflow_amount > 100 else Severity.MEDIUM

            findings.append(
                Finding(
                    title=f"Horizontal overflow: content {overflow_amount}px wider than viewport",
                    severity=severity,
                    category=Category.LAYOUT,
                    url=page_state.url,
                    viewport=page_state.viewport,
                    description=(
                        f"The page has horizontal scrolling, indicating content overflows the viewport.\n\n"
                        f"Scroll width: {scroll_width}px\n"
                        f"Viewport width: {client_width}px\n"
                        f"Overflow: {overflow_amount}px\n\n"
                        f"This is particularly problematic on mobile viewports where horizontal "
                        f"scrolling is unexpected and disrupts the user experience."
                    ),
                    steps_to_reproduce=[
                        f"Navigate to {page_state.url}",
                        f"Observe horizontal scrollbar at viewport: {page_state.viewport}",
                        "Scroll right to see overflowing content",
                    ],
                    expected="No horizontal scrollbar; content fits within viewport width",
                    actual=f"Content overflows viewport by {overflow_amount}px",
                    confidence=1.0,
                    screenshot=page_state.screenshot_path,
                    raw_evidence={"dom_metrics": metrics},
                )
            )

        return findings

    def _check_overflow_elements(self, page_state: PageState, overflow_elements: list[dict]) -> list[Finding]:
        findings = []
        if not overflow_elements:
            return findings

        # Report as a summary finding rather than per-element to avoid flooding
        element_descriptions = []
        for el in overflow_elements[:5]:
            tag = el.get("tag", "?")
            text = el.get("text", "")[:40]
            right = el.get("right", 0)
            element_descriptions.append(f"<{tag}> '{text}' (right edge: {right:.0f}px)")

        findings.append(
            Finding(
                title=f"{len(overflow_elements)} element(s) appear outside viewport bounds",
                severity=Severity.MEDIUM,
                category=Category.LAYOUT,
                url=page_state.url,
                viewport=page_state.viewport,
                description=(
                    f"{len(overflow_elements)} DOM element(s) have bounding boxes that extend beyond "
                    f"the visible viewport area. These elements may be clipped or invisible to users.\n\n"
                    f"Affected elements:\n" + "\n".join(element_descriptions)
                ),
                steps_to_reproduce=[
                    f"Navigate to {page_state.url}",
                    "Open browser developer tools",
                    "Inspect the elements listed above",
                ],
                expected="All visible content should fit within the viewport",
                actual=f"{len(overflow_elements)} element(s) extend outside viewport bounds",
                confidence=0.85,
                screenshot=page_state.screenshot_path,
                raw_evidence={"overflow_elements": overflow_elements},
            )
        )
        return findings

    def _check_interactive_elements(self, page_state: PageState) -> list[Finding]:
        findings = []
        invisible_count = 0
        too_small_count = 0

        for element in page_state.visible_elements:
            bb = element.bounding_box
            if not bb:
                continue

            width = bb.get("width", 0)
            height = bb.get("height", 0)

            # Interactive element with zero dimensions
            if width == 0 or height == 0:
                if element.tag in ("button", "a", "input", "select"):
                    invisible_count += 1

            # Interactive element smaller than minimum recommended size
            elif (width < _MIN_INTERACTIVE_SIZE or height < _MIN_INTERACTIVE_SIZE):
                if element.tag in ("button", "input", "select"):
                    too_small_count += 1

        if invisible_count > 0:
            findings.append(
                Finding(
                    title=f"{invisible_count} interactive element(s) have zero dimensions",
                    severity=Severity.HIGH,
                    category=Category.LAYOUT,
                    url=page_state.url,
                    viewport=page_state.viewport,
                    description=(
                        f"{invisible_count} button(s), link(s), or input(s) have zero width or height. "
                        f"These elements are invisible and cannot be interacted with by users. "
                        f"This is a functional defect — the UI appears to have controls that don't exist."
                    ),
                    steps_to_reproduce=[
                        f"Navigate to {page_state.url}",
                        "Inspect interactive elements in the DOM",
                        "Find elements with 0px width or height",
                    ],
                    expected="All interactive elements should have visible dimensions",
                    actual=f"{invisible_count} interactive element(s) with zero dimensions",
                    confidence=0.9,
                    screenshot=page_state.screenshot_path,
                )
            )

        if too_small_count > 0:
            findings.append(
                Finding(
                    title=f"{too_small_count} interactive element(s) smaller than {_MIN_INTERACTIVE_SIZE}px",
                    severity=Severity.LOW,
                    category=Category.LAYOUT,
                    url=page_state.url,
                    viewport=page_state.viewport,
                    description=(
                        f"{too_small_count} interactive element(s) are smaller than {_MIN_INTERACTIVE_SIZE}px "
                        f"in at least one dimension. These are difficult to click, especially on touch devices. "
                        f"WCAG 2.5.5 recommends a minimum target size of 44x44px."
                    ),
                    steps_to_reproduce=[
                        f"Navigate to {page_state.url}",
                        f"Try to interact with small buttons/inputs on {page_state.viewport} viewport",
                    ],
                    expected=f"Interactive elements should be at least {_MIN_INTERACTIVE_SIZE}px in each dimension",
                    actual=f"{too_small_count} element(s) are smaller than minimum recommended size",
                    confidence=0.9,
                    screenshot=page_state.screenshot_path,
                )
            )

        return findings
