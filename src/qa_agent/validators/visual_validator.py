"""
VisualValidator — VLM-powered visual defect detection.

This is the AI-enhanced validator. It sends screenshots to the VLM
and translates structured VLM findings into Finding objects.

The VLM is an enhancement — not the entire system. If the VLM fails
or is unavailable, the other validators still produce useful results.
"""

from __future__ import annotations

from qa_agent.utils.schemas import Category, Finding, PageState, Severity
from qa_agent.validators.base import BaseValidator
from qa_agent.vision.base import VisionProvider, VisualAnalysisResult

_SEVERITY_MAP = {
    "CRITICAL": Severity.CRITICAL,
    "HIGH": Severity.HIGH,
    "MEDIUM": Severity.MEDIUM,
    "LOW": Severity.LOW,
    "INFO": Severity.INFO,
}


class VisualValidator(BaseValidator):
    """
    Sends page screenshots to VLM for visual defect analysis.
    Converts VLM findings into structured Finding objects.
    """

    def __init__(self, vision_provider: VisionProvider):
        super().__init__()
        self.vision = vision_provider

    async def validate(self, page_state: PageState, **kwargs) -> list[Finding]:
        screenshot_b64: str | None = kwargs.get("screenshot_b64")
        if not screenshot_b64:
            self.logger.debug("VisualValidator: no screenshot provided, skipping")
            return []

        context = {
            "url": page_state.url,
            "title": page_state.title,
            "viewport": page_state.viewport,
            "console_errors": [m.text for m in page_state.console_errors[:5]],
            "network_errors": [
                f"{e.method} {e.url} → {e.status or e.error}"
                for e in page_state.network_failures[:5]
            ],
            "recent_actions": [
                a.to_human_step() for a in page_state.actions_performed[-5:]
            ],
        }

        try:
            result: VisualAnalysisResult = await self.vision.analyze_screenshot(
                screenshot_b64=screenshot_b64,
                context=context,
            )
        except Exception as e:
            self.logger.warning(f"VisualValidator: VLM call failed — {e}")
            return []

        if not result.analysis_successful:
            self.logger.warning(f"VisualValidator: analysis unsuccessful — {result.error}")
            return []

        findings = []
        for vf in result.findings:
            # Skip INFO-level findings with low confidence — they add noise
            if vf.severity == "INFO" and vf.confidence < 0.5:
                continue

            severity = _SEVERITY_MAP.get(vf.severity.upper(), Severity.INFO)

            finding = Finding(
                title=vf.title,
                severity=severity,
                category=Category.VISUAL,
                url=page_state.url,
                viewport=page_state.viewport,
                description=(
                    f"{vf.description}\n\n"
                    f"Location: {vf.location}\n"
                    f"Element: {vf.element_description or 'not specified'}\n"
                    f"VLM model: {result.model}"
                ),
                steps_to_reproduce=[
                    f"Navigate to {page_state.url}",
                    f"View the page at viewport: {page_state.viewport}",
                    f"Observe: {vf.location or 'the page'}",
                ],
                expected="Page should display correctly without visual defects",
                actual=vf.description[:200],
                confidence=vf.confidence,
                screenshot=page_state.screenshot_path,
                raw_evidence={
                    "vlm_finding": vf.model_dump(),
                    "vlm_model": result.model,
                    "overall_assessment": result.overall_assessment,
                },
            )
            findings.append(finding)

        self.logger.info(
            f"VisualValidator: {len(findings)} finding(s) on {page_state.url} @ {page_state.viewport}"
        )
        return findings
