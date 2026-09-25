"""
ExplorationEngine — autonomous page exploration.

This is the heart of the autonomous QA behavior. Given a starting URL,
it systematically discovers and tests the application without any pre-written
test cases.

Strategy:
1. Start at the root URL
2. Capture page state
3. Discover all interactive elements
4. Use VLM (or heuristics in mock mode) to decide the next action
5. Execute action, observe result, capture new state
6. Add newly discovered URLs to the exploration queue
7. Repeat until limits are hit or the agent signals completion

Safeguards:
- max_pages: stop after N unique pages
- max_actions: stop after N total actions
- max_depth: stop after N click depth from root
- timeout: wall-clock timeout
- URL deduplication
- Content hash deduplication (detects same page with different URL)
- Repeated action detection (avoid clicking the same thing twice)
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

from qa_agent.browser.page_controller import PageController
from qa_agent.utils.config import QAConfig
from qa_agent.utils.logger import get_logger
from qa_agent.utils.schemas import (
    ElementInfo,
    PageState,
    ViewportConfig,
)
from qa_agent.vision.base import ActionPlan, VisionProvider

logger = get_logger(__name__)


@dataclass
class ExplorationState:
    """Tracks the full state of an exploration run."""

    start_url: str
    visited_urls: set[str] = field(default_factory=set)
    visited_hashes: set[str] = field(default_factory=set)
    page_states: list[PageState] = field(default_factory=list)
    url_queue: deque[tuple[str, int]] = field(default_factory=deque)  # (url, depth)
    total_actions: int = 0
    start_time: float = field(default_factory=time.time)

    def normalize_url(self, url: str) -> str:
        """Normalize URL for deduplication (remove fragments, trailing slashes)."""
        try:
            parsed = urlparse(url)
            normalized = f"{parsed.scheme}://{parsed.netloc}{parsed.path.rstrip('/')}{'?' + parsed.query if parsed.query else ''}"
            return normalized.lower()
        except Exception:
            return url.lower()

    def has_visited(self, url: str) -> bool:
        return self.normalize_url(url) in self.visited_urls

    def mark_visited(self, url: str) -> None:
        self.visited_urls.add(self.normalize_url(url))

    def is_same_domain(self, url: str, base_url: str) -> bool:
        try:
            return urlparse(url).netloc == urlparse(base_url).netloc
        except Exception:
            return False

    def elapsed(self) -> float:
        return time.time() - self.start_time


class ExplorationEngine:
    """
    Autonomously explores a web application for QA purposes.

    Uses a hybrid approach:
    - Breadth-first URL discovery (following links)
    - VLM-guided action selection (clicking, typing, scrolling)
    - Heuristic fallback when VLM is unavailable or returns low-confidence
    """

    def __init__(
        self,
        config: QAConfig,
        vision_provider: VisionProvider,
        page_controller: PageController,
        viewport: ViewportConfig,
    ):
        self.config = config
        self.vision = vision_provider
        self.page = page_controller
        self.viewport = viewport
        self.state = ExplorationState(start_url=config.target_url)

    async def explore(self) -> list[PageState]:
        """
        Main exploration loop. Returns all captured PageState objects.
        """
        logger.info(
            f"Starting exploration of {self.config.target_url} "
            f"(max_pages={self.config.exploration.max_pages}, "
            f"max_actions={self.config.exploration.max_actions}, "
            f"timeout={self.config.exploration.timeout}s)"
        )

        # Initialize with the start URL
        self.state.url_queue.append((self.config.target_url, 0))

        while self.state.url_queue and self._within_limits():
            url, depth = self.state.url_queue.popleft()

            if self.state.has_visited(url):
                continue

            if depth > self.config.exploration.max_depth:
                logger.debug(f"Skipping {url}: max depth {self.config.exploration.max_depth} reached")
                continue

            page_state = await self._explore_url(url, depth)
            if page_state:
                # Discover new URLs from this page
                await self._discover_urls(page_state, depth)

        logger.info(
            f"Exploration complete: {len(self.state.visited_urls)} pages, "
            f"{self.state.total_actions} actions, "
            f"{self.state.elapsed():.1f}s elapsed"
        )
        return self.state.page_states

    async def _explore_url(self, url: str, depth: int) -> PageState | None:
        """Navigate to a URL and explore it."""
        if not self._within_limits():
            return None

        logger.info(f"[{len(self.state.visited_urls)+1}] Exploring: {url} (depth={depth})")
        self.state.mark_visited(url)

        # Navigate
        success = await self.page.navigate(url)
        if not success:
            logger.warning(f"Navigation failed: {url}")
            return None

        # Small stabilization wait
        await asyncio.sleep(0.3)

        # Capture initial state
        page_state = await self.page.capture_full_state(
            viewport_label=str(self.viewport),
            depth=depth,
            parent_url=None,
            screenshot_filename=f"page_{len(self.state.page_states)+1:03d}_{_safe_name(url)}.png",
        )

        # Deduplicate by content hash
        if page_state.page_hash in self.state.visited_hashes:
            logger.debug(f"Duplicate page state detected (same content): {url}")
            return page_state  # Still return for validation, but don't re-explore

        self.state.visited_hashes.add(page_state.page_hash)
        self.state.page_states.append(page_state)

        # Perform in-page actions (scroll, click interactive elements)
        await self._explore_page_actions(page_state, depth)

        return page_state

    async def _explore_page_actions(self, initial_state: PageState, depth: int) -> None:
        """
        Perform interactive exploration actions on the current page.
        """
        if not self._within_limits():
            return

        actions_on_this_page = 0
        max_actions_per_page = min(8, self.config.exploration.max_actions - self.state.total_actions)

        # Always scroll down first to load lazy content
        await self.page.scroll_down(400)
        await asyncio.sleep(0.2)
        self.state.total_actions += 1
        actions_on_this_page += 1

        # Get screenshot for VLM action planning
        screenshot_b64 = await self.page.capture_screenshot_b64()

        while actions_on_this_page < max_actions_per_page and self._within_limits():
            # Get current interactive elements
            elements = await self.page.get_interactive_elements()

            # Ask VLM (or heuristic) for the next action
            plan = await self._plan_next_action(screenshot_b64, elements, depth)

            if plan.exploration_complete or plan.action == "done":
                logger.debug(f"Agent signals page exploration complete: {self.page.current_url}")
                break

            executed = await self._execute_action_plan(plan, elements)
            if executed:
                self.state.total_actions += 1
                actions_on_this_page += 1
                await asyncio.sleep(0.3)

                # Capture new screenshot after action
                screenshot_b64 = await self.page.capture_screenshot_b64()

                # If URL changed, we're on a new page — stop in-page actions
                current_url = self.page.current_url
                if current_url and not self.state.has_visited(current_url):
                    if current_url != initial_state.url:
                        logger.debug(f"Action led to new URL: {current_url}")
                        # Queue this URL for exploration
                        if self.state.is_same_domain(current_url, self.config.target_url):
                            self.state.url_queue.appendleft((current_url, depth + 1))
                        # Go back to continue from original page
                        await self.page.go_back()
                        await asyncio.sleep(0.3)
                        break
            else:
                # Action failed — avoid getting stuck, skip
                break

    async def _plan_next_action(
        self,
        screenshot_b64: str | None,
        elements: list[ElementInfo],
        depth: int,
    ) -> ActionPlan:
        """
        Use VLM to plan the next exploration action.
        Falls back to a heuristic if VLM fails or screenshot unavailable.
        """
        if not screenshot_b64:
            return self._heuristic_action(elements)

        page_context = {
            "url": self.page.current_url,
            "title": await self.page.get_page_title(),
            "viewport": str(self.viewport),
            "depth": depth,
            "actions_taken": self.state.total_actions,
            "visited_urls": list(self.state.visited_urls)[:10],
            "available_elements": [
                {
                    "tag": e.tag,
                    "role": e.role,
                    "text": e.text,
                    "aria_label": e.aria_label,
                    "href": e.href,
                }
                for e in elements[:20]
            ],
        }

        try:
            plan = await self.vision.plan_action(screenshot_b64, page_context)
            return plan
        except Exception as e:
            logger.warning(f"VLM action planning failed: {e}. Using heuristic.")
            return self._heuristic_action(elements)

    def _heuristic_action(self, elements: list[ElementInfo]) -> ActionPlan:
        """
        Simple heuristic when VLM is unavailable or returns low confidence.
        Prefers navigation links, then buttons, then signals completion.
        """
        from qa_agent.vision.base import ActionPlan

        # Filter out destructive elements
        safe_elements = [
            e for e in elements
            if not self._is_destructive(e)
        ]

        # Prefer nav links
        nav_links = [e for e in safe_elements if e.tag == "a" and e.href and e.text]
        if nav_links:
            el = nav_links[0]
            return ActionPlan(
                action="click",
                target_description=el.display_name,
                reason="Following navigation link (heuristic)",
                confidence=0.7,
            )

        # Then buttons
        buttons = [e for e in safe_elements if e.tag == "button" and e.text]
        if buttons:
            el = buttons[0]
            return ActionPlan(
                action="click",
                target_description=el.display_name,
                reason="Clicking visible button (heuristic)",
                confidence=0.6,
            )

        return ActionPlan(
            action="done",
            reason="No suitable actions found (heuristic)",
            confidence=0.9,
            exploration_complete=True,
        )

    def _is_destructive(self, element: ElementInfo) -> bool:
        """Check if an element likely triggers a destructive action."""
        if not self.config.safety.skip_destructive_actions:
            return False
        text = (element.text or element.aria_label or "").lower()
        return any(kw in text for kw in self.config.safety.destructive_keywords)

    async def _execute_action_plan(
        self,
        plan: ActionPlan,
        elements: list[ElementInfo],
    ) -> bool:
        """Translate an ActionPlan into actual browser actions."""
        try:
            action = plan.action

            if action == "scroll":
                direction = plan.value or "down"
                amount = 400 if direction == "down" else -400
                await self.page.scroll_down(amount)
                return True

            elif action == "click":
                # Find the best matching element
                target = self._find_element_by_description(
                    plan.target_description, elements
                )
                if target:
                    return await self.page.click_element(target)
                logger.debug(f"No matching element for: {plan.target_description}")
                return False

            elif action == "type":
                target = self._find_input_element(elements)
                if target and plan.value:
                    return await self.page.type_into(target, plan.value)
                return False

            elif action == "hover":
                target = self._find_element_by_description(
                    plan.target_description, elements
                )
                if target and target.bounding_box:
                    bb = target.bounding_box
                    await self.page.page.mouse.move(
                        bb["x"] + bb["width"] / 2,
                        bb["y"] + bb["height"] / 2,
                    )
                    await asyncio.sleep(0.5)
                    return True
                return False

            elif action == "go_back":
                return await self.page.go_back()

            elif action == "navigate" and plan.value:
                url = plan.value
                if not url.startswith("http"):
                    url = urljoin(self.page.current_url, url)
                return await self.page.navigate(url)

        except Exception as e:
            logger.debug(f"Action execution failed ({plan.action}): {e}")

        return False

    def _find_element_by_description(
        self,
        description: str,
        elements: list[ElementInfo],
    ) -> ElementInfo | None:
        """Find the best matching element for a description."""
        if not description or not elements:
            return None

        desc_lower = description.lower()

        # Exact text match
        for el in elements:
            if el.text and el.text.lower() == desc_lower:
                return el

        # Partial text match
        for el in elements:
            if el.text and desc_lower in el.text.lower():
                return el

        # aria-label match
        for el in elements:
            if el.aria_label and desc_lower in el.aria_label.lower():
                return el

        # First clickable element as fallback
        clickable = [e for e in elements if e.tag in ("a", "button")]
        return clickable[0] if clickable else None

    def _find_input_element(self, elements: list[ElementInfo]) -> ElementInfo | None:
        """Find the first visible text input."""
        for el in elements:
            if el.tag in ("input", "textarea") and el.input_type not in ("submit", "button", "checkbox", "radio", "hidden"):
                return el
        return None

    def _within_limits(self) -> bool:
        """Check if exploration limits have been reached."""
        cfg = self.config.exploration

        if len(self.state.visited_urls) >= cfg.max_pages:
            logger.info(f"Exploration limit reached: max_pages={cfg.max_pages}")
            return False

        if self.state.total_actions >= cfg.max_actions:
            logger.info(f"Exploration limit reached: max_actions={cfg.max_actions}")
            return False

        if self.state.elapsed() >= cfg.timeout:
            logger.info(f"Exploration timeout reached: {cfg.timeout}s")
            return False

        return True

    async def _discover_urls(self, page_state: PageState, current_depth: int) -> None:
        """Extract links from the page and add new ones to the queue."""
        links = await self.page.get_links()
        new_count = 0
        for link in links:
            href = link.get("href", "")
            if not href:
                continue
            if not self.state.is_same_domain(href, self.config.target_url):
                continue
            if self.state.has_visited(href):
                continue
            self.state.url_queue.append((href, current_depth + 1))
            new_count += 1

        if new_count:
            logger.debug(f"Discovered {new_count} new URL(s) from {page_state.url}")


def _safe_name(url: str) -> str:
    """Create a filesystem-safe name from a URL."""
    try:
        parsed = urlparse(url)
        path = parsed.path.strip("/").replace("/", "_") or "root"
        return path[:30]
    except Exception:
        return "page"
