"""
ImageValidator — detects broken, empty, or missing images.

Uses DOM inspection data collected by PageController.get_images().
This is fully deterministic — no AI required.

Severity rules:
- naturalWidth == 0 and complete == true → broken image (failed to load) → MEDIUM
- src is empty/missing → MEDIUM
- Alt text missing on visible image → LOW (accessibility concern)
- Image in main content area with no alt → MEDIUM
"""

from __future__ import annotations

from qa_agent.utils.schemas import Category, Finding, PageState, Severity
from qa_agent.validators.base import BaseValidator


class ImageValidator(BaseValidator):
    """
    Validates image elements for broken sources and missing attributes.
    """

    async def validate(self, page_state: PageState, **kwargs) -> list[Finding]:
        images: list[dict] = kwargs.get("images", [])
        if not images:
            return []

        findings = []
        broken_count = 0
        missing_alt_count = 0

        for img in images:
            src = img.get("src", "") or img.get("current_src", "")
            complete = img.get("complete", True)
            natural_width = img.get("natural_width", 0)
            alt = img.get("alt")  # None means no alt attribute; "" is valid empty alt

            # Detect broken images: loaded but has 0 natural dimensions
            if complete and natural_width == 0 and src and not src.startswith("data:"):
                broken_count += 1
                if broken_count <= 10:  # Cap to avoid report flooding
                    findings.append(
                        Finding(
                            title=f"Broken image: {src[:60]}",
                            severity=Severity.MEDIUM,
                            category=Category.IMAGE,
                            url=page_state.url,
                            viewport=page_state.viewport,
                            description=(
                                f"An image failed to load. The browser reports 0x0 natural dimensions "
                                f"despite the load completing, indicating the image resource is broken or missing.\n\n"
                                f"Image source: {src}"
                            ),
                            steps_to_reproduce=[
                                f"Navigate to {page_state.url}",
                                "Observe the broken image placeholder",
                                f"Inspect image src: {src}",
                            ],
                            expected="Image should load and display with non-zero dimensions",
                            actual=f"Image loaded but has 0x0 natural dimensions: {src[:80]}",
                            confidence=1.0,
                            screenshot=page_state.screenshot_path,
                            raw_evidence={"image": img},
                        )
                    )

            # Detect missing src
            elif not src:
                findings.append(
                    Finding(
                        title="Image element with missing src attribute",
                        severity=Severity.MEDIUM,
                        category=Category.IMAGE,
                        url=page_state.url,
                        viewport=page_state.viewport,
                        description=(
                            "An <img> element has no src attribute or an empty src. "
                            "This will result in a broken image placeholder visible to users."
                        ),
                        steps_to_reproduce=[
                            f"Navigate to {page_state.url}",
                            "Inspect <img> elements in the DOM",
                            "Find the element with no src attribute",
                        ],
                        expected="All <img> elements should have a valid src attribute",
                        actual="<img> element found with empty or missing src",
                        confidence=1.0,
                        screenshot=page_state.screenshot_path,
                    )
                )

            # Detect missing alt text (accessibility — LOW severity)
            if alt is None and src and natural_width > 0:
                missing_alt_count += 1

        # Report missing alt text as a summary rather than per-image
        if missing_alt_count > 0:
            findings.append(
                Finding(
                    title=f"Missing alt text on {missing_alt_count} image(s)",
                    severity=Severity.LOW,
                    category=Category.IMAGE,
                    url=page_state.url,
                    viewport=page_state.viewport,
                    description=(
                        f"{missing_alt_count} image(s) on this page have no alt attribute. "
                        f"Alt text is required for screen readers and is a WCAG 2.1 Level A requirement. "
                        f"Images that are purely decorative should use alt=\"\"."
                    ),
                    steps_to_reproduce=[
                        f"Navigate to {page_state.url}",
                        "Inspect <img> elements",
                        f"Find {missing_alt_count} element(s) missing the alt attribute",
                    ],
                    expected="All informational images should have descriptive alt text",
                    actual=f"{missing_alt_count} image(s) missing alt attribute",
                    confidence=1.0,
                    screenshot=page_state.screenshot_path,
                )
            )

        self.logger.info(f"ImageValidator: {len(findings)} finding(s) on {page_state.url}")
        return findings
