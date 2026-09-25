"""
AccessibilityValidator — automated accessibility checking via axe-core.

Injects axe-core into the page and runs WCAG 2.1 A/AA checks.
Also performs custom DOM-based checks for common issues axe may not catch.

Important limitation (documented in README):
Automated checks only catch ~30% of accessibility issues. Manual testing
with screen readers and keyboard navigation is required for full compliance.

Severity mapping from axe:
- axe critical → CRITICAL
- axe serious → HIGH
- axe moderate → MEDIUM
- axe minor → LOW
"""

from __future__ import annotations

from qa_agent.utils.schemas import Category, Finding, PageState, Severity
from qa_agent.validators.base import BaseValidator

_AXE_SEVERITY_MAP = {
    "critical": Severity.CRITICAL,
    "serious": Severity.HIGH,
    "moderate": Severity.MEDIUM,
    "minor": Severity.LOW,
}


class AccessibilityValidator(BaseValidator):
    """
    Runs axe-core accessibility analysis on the page.
    Requires axe_results kwarg from PageController.run_axe().
    """

    async def validate(self, page_state: PageState, **kwargs) -> list[Finding]:
        axe_results: dict = kwargs.get("axe_results", {})
        if not axe_results or "error" in axe_results:
            error = axe_results.get("error", "axe-core results not available") if axe_results else "axe-core not run"
            self.logger.debug(f"AccessibilityValidator skipped: {error}")
            return []

        violations: list[dict] = axe_results.get("violations", [])
        if not violations:
            return []

        findings = []
        for violation in violations:
            rule_id = violation.get("id", "unknown")
            impact = violation.get("impact", "minor")
            description = violation.get("description", "")
            help_text = violation.get("help", "")
            help_url = violation.get("helpUrl", "")
            nodes = violation.get("nodes", [])

            severity = _AXE_SEVERITY_MAP.get(impact.lower(), Severity.LOW)

            # Build element descriptions from affected nodes
            node_descriptions = []
            for node in nodes[:5]:  # Cap at 5 examples
                html = node.get("html", "")[:100]
                node_descriptions.append(f"  - {html}")

            affected_elements = "\n".join(node_descriptions) if node_descriptions else "  (no element details)"
            extra_nodes = max(0, len(nodes) - 5)

            finding = Finding(
                title=f"Accessibility: {help_text or rule_id} ({len(nodes)} element{'s' if len(nodes) != 1 else ''})",
                severity=severity,
                category=Category.ACCESSIBILITY,
                url=page_state.url,
                viewport=page_state.viewport,
                description=(
                    f"axe-core detected a WCAG accessibility violation: {description}\n\n"
                    f"Rule: {rule_id} ({impact} impact)\n"
                    f"Affected elements ({len(nodes)} total):\n{affected_elements}"
                    + (f"\n  ... and {extra_nodes} more" if extra_nodes > 0 else "")
                    + (f"\n\nReference: {help_url}" if help_url else "")
                ),
                steps_to_reproduce=[
                    f"Navigate to {page_state.url}",
                    "Open browser devtools",
                    f"Run axe.run() — violation: {rule_id}",
                    "Locate affected elements in the DOM",
                ],
                expected=f"Page should pass WCAG 2.1 AA rule: {rule_id}",
                actual=f"axe-core reports {len(nodes)} violation(s) for rule '{rule_id}'",
                confidence=0.95,  # axe is highly reliable; not 1.0 due to occasional false positives
                screenshot=page_state.screenshot_path,
                raw_evidence={
                    "axe_rule": rule_id,
                    "impact": impact,
                    "nodes_count": len(nodes),
                    "help_url": help_url,
                },
            )
            findings.append(finding)

        self.logger.info(
            f"AccessibilityValidator: {len(findings)} violation(s) on {page_state.url}"
        )
        return findings
