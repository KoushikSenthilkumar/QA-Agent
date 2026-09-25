"""
Baseline visual regression support.

Allows storing baseline screenshots and comparing future runs against them.

Usage:
    qa-agent baseline https://example.com  # Store baselines
    qa-agent compare https://example.com   # Compare against baselines

Comparison approach:
1. Pixel-level difference using numpy (fast, objective)
2. Difference threshold to ignore minor rendering differences
3. Save diff images highlighting changed areas
"""

from __future__ import annotations

import json
from pathlib import Path

from qa_agent.utils.logger import get_logger
from qa_agent.utils.schemas import Category, Finding, Severity

logger = get_logger(__name__)

# Threshold above which we report a visual regression
# 0.02 = 2% of pixels different → report
_DEFAULT_DIFF_THRESHOLD = 0.02


class BaselineManager:
    """Manages baseline screenshots for visual regression testing."""

    def __init__(self, baselines_dir: Path):
        self.baselines_dir = baselines_dir
        self.baselines_dir.mkdir(parents=True, exist_ok=True)
        self._index_path = baselines_dir / "index.json"
        self._index: dict = self._load_index()

    def _load_index(self) -> dict:
        if self._index_path.exists():
            try:
                return json.loads(self._index_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {"baselines": []}

    def _save_index(self) -> None:
        self._index_path.write_text(
            json.dumps(self._index, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def get_baseline_path(self, url: str, viewport: str) -> Path | None:
        """Get the stored baseline screenshot path for a URL+viewport."""
        key = f"{url}|{viewport}"
        for entry in self._index["baselines"]:
            if entry.get("key") == key:
                path = self.baselines_dir / entry["filename"]
                if path.exists():
                    return path
        return None

    async def store_baseline(
        self,
        screenshot_path: str,
        url: str,
        viewport: str,
    ) -> str:
        """Store a screenshot as the baseline for a URL+viewport pair."""
        import shutil

        filename = _baseline_filename(url, viewport)
        dest = self.baselines_dir / filename
        shutil.copy2(screenshot_path, dest)

        key = f"{url}|{viewport}"
        # Remove existing entry
        self._index["baselines"] = [
            e for e in self._index["baselines"] if e.get("key") != key
        ]
        self._index["baselines"].append(
            {"key": key, "filename": filename, "url": url, "viewport": viewport}
        )
        self._save_index()
        logger.info(f"Baseline stored: {dest}")
        return str(dest)

    async def compare(
        self,
        current_path: str,
        url: str,
        viewport: str,
        diff_dir: Path | None = None,
        threshold: float = _DEFAULT_DIFF_THRESHOLD,
    ) -> Finding | None:
        """
        Compare a current screenshot against the stored baseline.

        Returns a Finding if a significant visual regression is detected,
        or None if the comparison passes.
        """
        baseline_path = self.get_baseline_path(url, viewport)
        if not baseline_path:
            logger.debug(f"No baseline for {url} @ {viewport}")
            return None

        try:
            import numpy as np
            from PIL import Image
        except ImportError:
            logger.warning("numpy/Pillow not available — visual regression skipped")
            return None

        try:
            baseline_img = Image.open(baseline_path).convert("RGB")
            current_img = Image.open(current_path).convert("RGB")
        except Exception as e:
            logger.warning(f"Failed to open images for comparison: {e}")
            return None

        # Resize to same dimensions if different
        if baseline_img.size != current_img.size:
            current_img = current_img.resize(baseline_img.size, Image.LANCZOS)

        baseline_arr = np.array(baseline_img, dtype=np.float32)
        current_arr = np.array(current_img, dtype=np.float32)

        diff = np.abs(baseline_arr - current_arr)
        diff_normalized = diff / 255.0

        # Per-pixel mean difference
        per_pixel_mean = diff_normalized.mean(axis=2)  # Shape: H x W

        # Percentage of pixels that changed significantly (> 10% brightness diff)
        changed_pixels = (per_pixel_mean > 0.1).mean()

        if changed_pixels < threshold:
            return None

        # Save diff image
        diff_path = None
        if diff_dir:
            diff_dir.mkdir(parents=True, exist_ok=True)
            diff_img = Image.fromarray((diff_normalized * 255).astype("uint8"))
            diff_filename = _baseline_filename(url, viewport).replace(".png", "-diff.png")
            diff_path = diff_dir / diff_filename
            diff_img.save(str(diff_path))

        return Finding(
            title=f"Visual regression detected at {viewport}: {changed_pixels:.1%} pixels changed",
            severity=Severity.HIGH if changed_pixels > 0.15 else Severity.MEDIUM,
            category=Category.VISUAL,
            url=url,
            viewport=viewport,
            description=(
                f"A visual regression was detected at {viewport}. "
                f"{changed_pixels:.1%} of pixels differ from the stored baseline by more than 10%.\n\n"
                f"Baseline: {baseline_path}\n"
                f"Current: {current_path}"
                + (f"\nDiff image: {diff_path}" if diff_path else "")
            ),
            steps_to_reproduce=[
                f"View baseline screenshot at {baseline_path}",
                f"View current screenshot at {current_path}",
                "Compare the two screenshots",
            ],
            expected="Page should match the stored visual baseline",
            actual=f"{changed_pixels:.1%} of pixels differ from baseline",
            confidence=0.85,
            screenshot=current_path,
            raw_evidence={
                "changed_pixels_pct": float(changed_pixels),
                "threshold": threshold,
                "baseline_path": str(baseline_path),
            },
        )


def _baseline_filename(url: str, viewport: str) -> str:
    """Create a deterministic filename for a baseline screenshot."""
    import hashlib
    key = f"{url}|{viewport}"
    hash_part = hashlib.md5(key.encode()).hexdigest()[:10]
    safe_url = url.replace("https://", "").replace("http://", "").replace("/", "_")[:30]
    safe_vp = viewport.replace("x", "x")
    return f"baseline_{safe_url}_{safe_vp}_{hash_part}.png"
