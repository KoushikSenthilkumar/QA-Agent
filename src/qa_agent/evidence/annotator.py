"""
ScreenshotAnnotator — draws bounding boxes and labels on screenshots.

Uses Pillow for image manipulation.
Creates annotated versions of screenshots with:
- Colored bounding boxes around problem areas
- Bug ID labels
- Severity color coding

Color coding:
- CRITICAL: Red (#FF0000)
- HIGH: Orange (#FF6600)
- MEDIUM: Yellow (#FFCC00)
- LOW: Blue (#0066FF)
- INFO: Gray (#888888)
"""

from __future__ import annotations

from pathlib import Path

from qa_agent.utils.logger import get_logger
from qa_agent.utils.schemas import Finding, Severity

logger = get_logger(__name__)

_SEVERITY_COLORS = {
    Severity.CRITICAL: "#FF0000",
    Severity.HIGH: "#FF6600",
    Severity.MEDIUM: "#FFCC00",
    Severity.LOW: "#0066FF",
    Severity.INFO: "#888888",
}


class ScreenshotAnnotator:
    """
    Annotates screenshots with visual markers for detected issues.
    """

    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

    async def annotate(
        self,
        source_path: str,
        finding: Finding,
        region: dict | None = None,  # {x, y, width, height} in pixels; None = full-page banner
    ) -> str | None:
        """
        Create an annotated copy of a screenshot.

        Args:
            source_path: Path to the original screenshot
            finding: The Finding to annotate
            region: Optional bounding box to highlight. If None, adds a banner.

        Returns:
            Path to the annotated screenshot, or None on failure.
        """
        try:
            from PIL import Image, ImageDraw, ImageFont
        except ImportError:
            logger.warning("Pillow not available — screenshot annotation skipped")
            return None

        source = Path(source_path)
        if not source.exists():
            logger.warning(f"Source screenshot not found: {source_path}")
            return None

        try:
            img = Image.open(source).convert("RGB")
        except Exception as e:
            logger.warning(f"Failed to open screenshot for annotation: {e}")
            return None

        draw = ImageDraw.Draw(img, "RGBA")
        color = _SEVERITY_COLORS.get(finding.severity, "#888888")
        color_rgba = self._hex_to_rgba(color, alpha=200)
        color_fill_rgba = self._hex_to_rgba(color, alpha=40)

        if region and all(k in region for k in ("x", "y", "width", "height")):
            # Draw bounding box around the specific region
            x, y = int(region["x"]), int(region["y"])
            w, h = int(region["width"]), int(region["height"])
            x2, y2 = x + w, y + h

            # Ensure bounds are within image
            img_w, img_h = img.size
            x = max(0, min(x, img_w))
            y = max(0, min(y, img_h))
            x2 = max(0, min(x2, img_w))
            y2 = max(0, min(y2, img_h))

            # Semi-transparent fill
            draw.rectangle([x, y, x2, y2], fill=color_fill_rgba, outline=color_rgba, width=3)

            # Label background
            label = f"{finding.id}"
            label_x = max(0, x)
            label_y = max(0, y - 22)
            draw.rectangle([label_x, label_y, label_x + len(label) * 8 + 10, label_y + 20], fill=color_rgba)
            draw.text((label_x + 5, label_y + 2), label, fill="white")
        else:
            # No bounding box — add a banner at the top with the finding ID and title
            banner_h = 36
            img_w = img.width
            draw.rectangle([0, 0, img_w, banner_h], fill=color_rgba)
            banner_text = f"{finding.id}: {finding.title[:80]}"
            draw.text((8, 8), banner_text, fill="white")

        # Save annotated version
        output_filename = f"{finding.id}-annotated.png"
        output_path = self.output_dir / output_filename
        try:
            img.save(str(output_path))
            return str(output_path)
        except Exception as e:
            logger.warning(f"Failed to save annotated screenshot: {e}")
            return None

    @staticmethod
    def _hex_to_rgba(hex_color: str, alpha: int = 255) -> tuple[int, int, int, int]:
        """Convert hex color string to RGBA tuple."""
        hex_color = hex_color.lstrip("#")
        r, g, b = int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)
        return (r, g, b, alpha)
