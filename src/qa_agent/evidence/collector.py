"""
EvidenceCollector — manages all evidence artifacts for a QA run.

Handles:
- Screenshot storage and naming conventions
- Console log export
- Network log export
- Video artifact management
- Evidence path tracking per finding
"""

from __future__ import annotations

import json
from pathlib import Path

import aiofiles

from qa_agent.utils.logger import get_logger
from qa_agent.utils.schemas import ConsoleMessage, Finding, NetworkEvent, PageState

logger = get_logger(__name__)


class EvidenceCollector:
    """
    Manages evidence artifacts for a QA run.
    All evidence is stored under a run-specific directory.
    """

    def __init__(self, screenshots_dir: Path, logs_dir: Path, recordings_dir: Path):
        self.screenshots_dir = screenshots_dir
        self.logs_dir = logs_dir
        self.recordings_dir = recordings_dir

        self.screenshots_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.recordings_dir.mkdir(parents=True, exist_ok=True)

    def get_screenshot_path(self, filename: str) -> Path:
        return self.screenshots_dir / filename

    async def save_console_log(
        self,
        messages: list[ConsoleMessage],
        run_id: str,
    ) -> str:
        """Export console messages to a JSON log file."""
        path = self.logs_dir / f"console_{run_id}.json"
        data = [m.model_dump() for m in messages]
        async with aiofiles.open(path, "w", encoding="utf-8") as f:
            await f.write(json.dumps(data, indent=2, ensure_ascii=False))
        logger.debug(f"Console log saved: {path}")
        return str(path)

    async def save_network_log(
        self,
        events: list[NetworkEvent],
        run_id: str,
    ) -> str:
        """Export network events to a JSON log file."""
        path = self.logs_dir / f"network_{run_id}.json"
        data = [e.model_dump() for e in events]
        async with aiofiles.open(path, "w", encoding="utf-8") as f:
            await f.write(json.dumps(data, indent=2, ensure_ascii=False))
        logger.debug(f"Network log saved: {path}")
        return str(path)

    async def save_page_states(
        self,
        page_states: list[PageState],
        run_id: str,
    ) -> str:
        """Export page states for debugging and analysis."""
        path = self.logs_dir / f"page_states_{run_id}.json"
        # Serialize, excluding binary/large fields
        data = []
        for ps in page_states:
            d = ps.model_dump()
            d.pop("accessibility_tree", None)  # Can be very large
            data.append(d)
        async with aiofiles.open(path, "w", encoding="utf-8") as f:
            await f.write(json.dumps(data, indent=2, ensure_ascii=False, default=str))
        return str(path)

    async def link_evidence_to_finding(
        self,
        finding: Finding,
        screenshot_path: str | None,
        annotated_screenshot_path: str | None = None,
        video_timestamp: float | None = None,
    ) -> Finding:
        """Attach evidence paths to a Finding object."""
        if screenshot_path:
            finding.screenshot = screenshot_path
        if annotated_screenshot_path:
            finding.annotated_screenshot = annotated_screenshot_path
        if video_timestamp is not None:
            finding.video_timestamp = video_timestamp
        return finding

    def get_relative_path(self, absolute_path: str | None, base_dir: Path) -> str | None:
        """Convert absolute path to relative (for use in HTML reports)."""
        if not absolute_path:
            return None
        try:
            return str(Path(absolute_path).relative_to(base_dir.parent))
        except ValueError:
            return absolute_path
