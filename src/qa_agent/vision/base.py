"""
VisionProvider abstract base class.

All AI vision providers implement this interface.
This ensures the rest of the codebase never imports OpenAI directly —
it always goes through this abstraction.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, Field


class VisualFinding(BaseModel):
    """A single visual defect identified by the VLM."""

    type: str = "visual_defect"
    severity: str  # CRITICAL / HIGH / MEDIUM / LOW / INFO
    title: str
    description: str
    location: str = ""  # Region description, e.g. "top navigation bar"
    confidence: float = 0.0
    bounding_box: dict[str, float] | None = None  # {x, y, width, height} if determinable
    element_description: str = ""


class VisualAnalysisResult(BaseModel):
    """Structured result from a VLM screenshot analysis."""

    findings: list[VisualFinding] = Field(default_factory=list)
    overall_assessment: str = ""
    page_summary: str = ""
    raw_response: str = ""
    provider: str = "unknown"
    model: str = "unknown"
    analysis_successful: bool = True
    error: str | None = None


class ActionPlan(BaseModel):
    """
    A structured action plan returned by the VLM's action-planning prompt.

    The VLM reasons about the current page state and recommends the next action.
    The exploration engine translates this into actual Playwright interactions.
    """

    action: str  # click / type / scroll / hover / navigate / go_back / done
    target_description: str = ""  # Human description of target element
    value: str | None = None  # For type actions
    reason: str = ""  # VLM's justification
    confidence: float = 0.0
    exploration_complete: bool = False  # VLM signals page is fully explored


class VisionProvider(ABC):
    """
    Abstract base for all VLM providers.

    Implementors: OpenAIProvider, MockVisionProvider
    """

    @abstractmethod
    async def analyze_screenshot(
        self,
        screenshot_b64: str,
        context: dict[str, Any],
    ) -> VisualAnalysisResult:
        """
        Analyze a screenshot for visual defects.

        Args:
            screenshot_b64: Base64-encoded PNG screenshot
            context: Page context dict with keys:
                - url: current URL
                - title: page title
                - viewport: viewport string
                - console_errors: list of console error strings
                - network_errors: list of network error strings
                - recent_actions: list of recent action descriptions
        """
        ...

    @abstractmethod
    async def plan_action(
        self,
        screenshot_b64: str,
        page_context: dict[str, Any],
    ) -> ActionPlan:
        """
        Given the current page screenshot, plan the next exploration action.

        Args:
            screenshot_b64: Base64-encoded PNG screenshot
            page_context: Dict with url, title, viewport, available_elements,
                         visited_urls, actions_taken, exploration_goal
        """
        ...

    @abstractmethod
    async def describe_bug(
        self,
        screenshot_b64: str,
        finding_context: dict[str, Any],
    ) -> str:
        """
        Generate a natural-language description of a detected bug.
        Used to enrich Finding.description with VLM reasoning.
        """
        ...
