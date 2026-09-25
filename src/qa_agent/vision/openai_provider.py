"""
OpenAIProvider — GPT-4o multimodal vision provider.

Uses GPT-4o's vision capabilities to analyze screenshots and plan actions.
All prompts are loaded from the prompts/ directory (not inline).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from qa_agent.utils.logger import get_logger
from qa_agent.vision.base import (
    ActionPlan,
    VisualAnalysisResult,
    VisualFinding,
    VisionProvider,
)

logger = get_logger(__name__)

# Prompt file locations
_PROMPTS_DIR = Path(__file__).parent.parent / "prompts"


def _load_prompt(name: str) -> str:
    path = _PROMPTS_DIR / name
    if path.exists():
        return path.read_text(encoding="utf-8")
    logger.warning(f"Prompt file not found: {path}")
    return ""


class OpenAIProvider(VisionProvider):
    """
    GPT-4o multimodal provider for visual QA analysis and action planning.

    Requires OPENAI_API_KEY environment variable (set via .env or VLM_API_KEY).
    """

    def __init__(self, model: str = "gpt-4o", max_tokens: int = 2048, temperature: float = 0.1, timeout: int = 30):
        try:
            from openai import AsyncOpenAI
        except ImportError:
            raise ImportError("openai package required: pip install openai")

        api_key = os.getenv("VLM_API_KEY") or os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError(
                "VLM_API_KEY or OPENAI_API_KEY environment variable must be set for OpenAI provider.\n"
                "Set VLM_PROVIDER=mock in .env to use mock mode without API credits."
            )

        self._client = AsyncOpenAI(api_key=api_key, timeout=timeout)
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature

        self._visual_inspection_prompt = _load_prompt("visual_inspection.txt")
        self._action_planning_prompt = _load_prompt("action_planning.txt")
        self._bug_analysis_prompt = _load_prompt("bug_analysis.txt")

        logger.info(f"OpenAIProvider ready: model={model}")

    async def analyze_screenshot(
        self,
        screenshot_b64: str,
        context: dict[str, Any],
    ) -> VisualAnalysisResult:
        """Send screenshot + context to GPT-4o for visual QA analysis."""
        system_prompt = self._visual_inspection_prompt or _default_visual_prompt()

        user_content = [
            {
                "type": "text",
                "text": (
                    f"URL: {context.get('url', 'unknown')}\n"
                    f"Title: {context.get('title', 'unknown')}\n"
                    f"Viewport: {context.get('viewport', 'unknown')}\n"
                    f"Console errors: {json.dumps(context.get('console_errors', [])[:5])}\n"
                    f"Network errors: {json.dumps(context.get('network_errors', [])[:5])}\n"
                    f"Recent actions: {json.dumps(context.get('recent_actions', [])[:5])}\n\n"
                    "Analyze this screenshot for visual/UI defects. "
                    "Return a JSON object matching the specified schema."
                ),
            },
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/png;base64,{screenshot_b64}",
                    "detail": "high",
                },
            },
        ]

        try:
            response = await self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                response_format={"type": "json_object"},
            )

            raw = response.choices[0].message.content or "{}"
            return self._parse_visual_response(raw)

        except Exception as e:
            logger.error(f"OpenAI analyze_screenshot failed: {e}")
            return VisualAnalysisResult(
                analysis_successful=False,
                error=str(e),
                provider="openai",
                model=self.model,
            )

    async def plan_action(
        self,
        screenshot_b64: str,
        page_context: dict[str, Any],
    ) -> ActionPlan:
        """Ask GPT-4o to plan the next exploration action."""
        system_prompt = self._action_planning_prompt or _default_action_prompt()

        elements_desc = "\n".join(
            f"- {e.get('tag', '?')} | {e.get('role', '?')} | text: '{e.get('text', '')[:60]}' | label: '{e.get('aria_label', '')}'"
            for e in page_context.get("available_elements", [])[:20]
        )

        user_content = [
            {
                "type": "text",
                "text": (
                    f"URL: {page_context.get('url', 'unknown')}\n"
                    f"Title: {page_context.get('title', 'unknown')}\n"
                    f"Viewport: {page_context.get('viewport', 'unknown')}\n"
                    f"Depth: {page_context.get('depth', 0)}\n"
                    f"Actions taken so far: {page_context.get('actions_taken', 0)}\n"
                    f"Visited URLs: {json.dumps(page_context.get('visited_urls', [])[:10])}\n"
                    f"Available interactive elements:\n{elements_desc}\n\n"
                    "What is the best next action to explore this page for QA purposes? "
                    "Return a JSON object matching the ActionPlan schema."
                ),
            },
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/png;base64,{screenshot_b64}",
                    "detail": "low",
                },
            },
        ]

        try:
            response = await self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                max_tokens=512,
                temperature=self.temperature,
                response_format={"type": "json_object"},
            )

            raw = response.choices[0].message.content or "{}"
            return self._parse_action_response(raw)

        except Exception as e:
            logger.error(f"OpenAI plan_action failed: {e}")
            return ActionPlan(
                action="done",
                reason=f"Action planning failed: {e}",
                confidence=0.0,
                exploration_complete=True,
            )

    async def describe_bug(
        self,
        screenshot_b64: str,
        finding_context: dict[str, Any],
    ) -> str:
        """Generate a natural-language bug description from a screenshot."""
        try:
            response = await self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": "You are a QA engineer writing concise, factual bug descriptions.",
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    f"A {finding_context.get('category')} issue was detected at "
                                    f"{finding_context.get('url')} on viewport {finding_context.get('viewport')}.\n"
                                    f"Finding: {finding_context.get('title')}\n"
                                    "Write a 2-3 sentence description of what you observe in this screenshot "
                                    "that confirms or explains this issue."
                                ),
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/png;base64,{screenshot_b64}",
                                    "detail": "low",
                                },
                            },
                        ],
                    },
                ],
                max_tokens=200,
                temperature=0.2,
            )
            return response.choices[0].message.content or ""
        except Exception as e:
            logger.warning(f"describe_bug failed: {e}")
            return ""

    def _parse_visual_response(self, raw: str) -> VisualAnalysisResult:
        try:
            data = json.loads(raw)
            findings = []
            for f in data.get("findings", []):
                findings.append(
                    VisualFinding(
                        type=f.get("type", "visual_defect"),
                        severity=f.get("severity", "INFO").upper(),
                        title=f.get("title", "Visual issue"),
                        description=f.get("description", ""),
                        location=f.get("location", ""),
                        confidence=float(f.get("confidence", 0.5)),
                        element_description=f.get("element_description", ""),
                    )
                )
            return VisualAnalysisResult(
                findings=findings,
                overall_assessment=data.get("overall_assessment", ""),
                page_summary=data.get("page_summary", ""),
                raw_response=raw,
                provider="openai",
                model=self.model,
                analysis_successful=True,
            )
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse VLM visual response: {e}")
            return VisualAnalysisResult(
                analysis_successful=False,
                error=f"JSON parse error: {e}",
                raw_response=raw,
                provider="openai",
                model=self.model,
            )

    def _parse_action_response(self, raw: str) -> ActionPlan:
        try:
            data = json.loads(raw)
            return ActionPlan(
                action=data.get("action", "done"),
                target_description=data.get("target_description", ""),
                value=data.get("value"),
                reason=data.get("reason", ""),
                confidence=float(data.get("confidence", 0.5)),
                exploration_complete=data.get("action", "") == "done",
            )
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse VLM action response: {e}")
            return ActionPlan(
                action="done",
                reason=f"Parse error: {e}",
                confidence=0.0,
                exploration_complete=True,
            )


def _default_visual_prompt() -> str:
    return """You are an expert QA engineer analyzing a web page screenshot for visual and UI defects.

Identify any of: misaligned elements, overlapping components, text clipping, broken layouts, 
invisible buttons, overflow issues, inconsistent spacing, unexpected empty areas, broken navigation,
poor responsive behavior, or any other visual defects a human tester would notice.

Respond with a JSON object in this exact schema:
{
  "findings": [
    {
      "type": "visual_defect",
      "severity": "CRITICAL|HIGH|MEDIUM|LOW|INFO",
      "title": "short title",
      "description": "detailed description",
      "location": "area of screen",
      "confidence": 0.0-1.0,
      "element_description": "what element is affected"
    }
  ],
  "overall_assessment": "one sentence summary",
  "page_summary": "brief page description"
}

If no defects are found, return an empty findings array with an INFO-level note.
Be factual. Do not hallucinate defects you cannot see. Rate confidence honestly."""


def _default_action_prompt() -> str:
    return """You are an autonomous QA agent deciding the next action to explore a web application.

Your goal is to maximize test coverage by visiting new pages, interacting with UI elements,
and discovering potential issues. Avoid revisiting the same states.

Respond with a JSON object in this exact schema:
{
  "action": "click|type|scroll|hover|navigate|go_back|done",
  "target_description": "human description of target element",
  "value": "text to type (for type action) or null",
  "reason": "why this action increases test coverage",
  "confidence": 0.0-1.0
}

Use "done" when the page has been sufficiently explored.
Prefer clicking navigation items, links, and buttons over form submission.
Never interact with elements that could cause destructive actions (delete, purchase, send)."""
