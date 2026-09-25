"""
PageController — High-level page interaction and state capture.

Wraps a Playwright Page to provide:
- Safe navigation with error handling
- Console and network event collection
- Screenshot capture
- Accessibility tree extraction
- Element discovery (using accessibility tree + DOM, not manual selectors)
- DOM metric collection (overflow, layout info)
- JavaScript injection helpers

The key design principle here: elements are discovered by their semantic
properties (role, text, aria-label), NOT by CSS selectors written by developers.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from playwright.async_api import (
    ConsoleMessage as PlaywrightConsoleMessage,
    Page,
    Request,
    Response,
    Error,
)

from qa_agent.utils.logger import get_logger
from qa_agent.utils.schemas import (
    ActionRecord,
    ActionType,
    ConsoleMessage,
    ElementInfo,
    NetworkEvent,
    PageState,
)

logger = get_logger(__name__)

# JavaScript snippets kept here for clarity
_JS_GET_DOM_METRICS = """
() => {
    const body = document.body;
    const html = document.documentElement;
    return {
        scroll_width: Math.max(body.scrollWidth, html.scrollWidth),
        scroll_height: Math.max(body.scrollHeight, html.scrollHeight),
        client_width: html.clientWidth,
        client_height: html.clientHeight,
        has_horizontal_scroll: body.scrollWidth > html.clientWidth,
        overflow_x: getComputedStyle(body).overflowX,
        overflow_y: getComputedStyle(body).overflowY,
    };
}
"""

_JS_GET_OVERFLOW_ELEMENTS = """
() => {
    const results = [];
    const viewport = { w: window.innerWidth, h: window.innerHeight };
    const elements = document.querySelectorAll('*');
    for (const el of elements) {
        const rect = el.getBoundingClientRect();
        if (rect.width === 0 && rect.height === 0) continue;
        if (rect.right > viewport.w + 10 || rect.bottom > viewport.h + 500) {
            const tag = el.tagName.toLowerCase();
            const text = (el.textContent || '').trim().substring(0, 80);
            const classes = (el.className || '').toString().substring(0, 80);
            results.push({ tag, text, classes, rect: { x: rect.x, y: rect.y, w: rect.width, h: rect.height }, right: rect.right, bottom: rect.bottom });
        }
        if (results.length >= 20) break;
    }
    return results;
}
"""

_JS_GET_INTERACTIVE_ELEMENTS = """
() => {
    const selectors = [
        'a[href]', 'button', 'input', 'select', 'textarea',
        '[role="button"]', '[role="link"]', '[role="menuitem"]',
        '[role="tab"]', '[role="checkbox"]', '[role="radio"]',
        '[onclick]', '[tabindex]:not([tabindex="-1"])',
        'summary', '[role="combobox"]', '[role="listbox"]'
    ];
    const seen = new Set();
    const results = [];
    for (const sel of selectors) {
        for (const el of document.querySelectorAll(sel)) {
            if (seen.has(el)) continue;
            seen.add(el);
            const rect = el.getBoundingClientRect();
            const isVisible = rect.width > 0 && rect.height > 0 &&
                getComputedStyle(el).visibility !== 'hidden' &&
                getComputedStyle(el).display !== 'none' &&
                getComputedStyle(el).opacity !== '0';
            if (!isVisible) continue;
            results.push({
                tag: el.tagName.toLowerCase(),
                role: el.getAttribute('role') || el.tagName.toLowerCase(),
                text: (el.textContent || el.value || '').trim().substring(0, 100),
                aria_label: el.getAttribute('aria-label'),
                placeholder: el.getAttribute('placeholder'),
                href: el.getAttribute('href'),
                input_type: el.getAttribute('type'),
                bounding_box: { x: rect.x, y: rect.y, width: rect.width, height: rect.height },
            });
            if (results.length >= 60) break;
        }
        if (results.length >= 60) break;
    }
    return results;
}
"""

_JS_GET_IMAGES = """
() => {
    return Array.from(document.querySelectorAll('img')).map(img => ({
        src: img.src,
        alt: img.getAttribute('alt'),
        natural_width: img.naturalWidth,
        natural_height: img.naturalHeight,
        complete: img.complete,
        current_src: img.currentSrc,
        bounding_box: (() => { const r = img.getBoundingClientRect(); return { x: r.x, y: r.y, w: r.width, h: r.height }; })()
    }));
}
"""

_JS_GET_LINKS = """
() => {
    return Array.from(document.querySelectorAll('a[href]')).map(a => ({
        href: a.href,
        text: (a.textContent || '').trim().substring(0, 100),
        is_external: a.hostname !== location.hostname,
    })).filter(l => l.href && !l.href.startsWith('javascript:') && !l.href.startsWith('mailto:') && !l.href.startsWith('tel:'));
}
"""


class PageController:
    """
    Wraps a Playwright Page with QA-specific capabilities.

    Collects console messages, network events, and page state.
    Provides safe wrappers around common interactions.
    """

    def __init__(self, page: Page, screenshots_dir: Path | None = None):
        self.page = page
        self.screenshots_dir = screenshots_dir or Path("./screenshots")
        self._console_messages: list[ConsoleMessage] = []
        self._network_events: list[NetworkEvent] = []
        self._action_history: list[ActionRecord] = []
        self._is_monitoring = False
        self._request_timing: dict[str, float] = {}

    def start_monitoring(self) -> None:
        """Attach event listeners for console and network monitoring."""
        if self._is_monitoring:
            return

        self.page.on("console", self._on_console)
        self.page.on("pageerror", self._on_page_error)
        self.page.on("request", self._on_request)
        self.page.on("response", self._on_response)
        self.page.on("requestfailed", self._on_request_failed)
        self._is_monitoring = True
        logger.debug("Page monitoring started")

    def stop_monitoring(self) -> None:
        """Remove event listeners."""
        if not self._is_monitoring:
            return
        self.page.remove_listener("console", self._on_console)
        self.page.remove_listener("pageerror", self._on_page_error)
        self.page.remove_listener("request", self._on_request)
        self.page.remove_listener("response", self._on_response)
        self.page.remove_listener("requestfailed", self._on_request_failed)
        self._is_monitoring = False

    def _on_console(self, msg: PlaywrightConsoleMessage) -> None:
        cm = ConsoleMessage(
            level=msg.type,
            text=msg.text,
            url=self.page.url,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        self._console_messages.append(cm)
        if msg.type in ("error", "assert"):
            logger.debug(f"Console error: {msg.text[:120]}")

    def _on_page_error(self, error: Error) -> None:
        cm = ConsoleMessage(
            level="error",
            text=f"Uncaught exception: {error.message}",
            url=self.page.url,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        self._console_messages.append(cm)
        logger.debug(f"Page error: {error.message[:120]}")

    def _on_request(self, request: Request) -> None:
        self._request_timing[request.url] = asyncio.get_event_loop().time()

    def _on_response(self, response: Response) -> None:
        status = response.status
        if status >= 400:
            ne = NetworkEvent(
                url=response.url,
                method=response.request.method,
                status=status,
                resource_type=response.request.resource_type,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
            self._network_events.append(ne)
            logger.debug(f"HTTP {status}: {response.url[:80]}")

    def _on_request_failed(self, request: Request) -> None:
        ne = NetworkEvent(
            url=request.url,
            method=request.method,
            resource_type=request.resource_type,
            error=request.failure or "Request failed",
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        self._network_events.append(ne)
        logger.debug(f"Request failed: {request.url[:80]}")

    # ─────────────────────────────────────────────────────────────────────────
    # Navigation
    # ─────────────────────────────────────────────────────────────────────────

    async def navigate(self, url: str, timeout: int = 30000) -> bool:
        """Navigate to a URL. Returns True on success."""
        url_before = self.page.url
        try:
            await self.page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=timeout,
            )
            # Wait a moment for dynamic content
            await asyncio.sleep(0.5)
            try:
                await self.page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                pass  # networkidle can time out on dynamic apps — that's okay

            self._record_action(
                ActionType.NAVIGATE,
                target_description=url,
                url_before=url_before,
                url_after=self.page.url,
                success=True,
            )
            return True
        except Exception as e:
            logger.warning(f"Navigation failed: {url} — {e}")
            self._record_action(
                ActionType.NAVIGATE,
                target_description=url,
                url_before=url_before,
                success=False,
                error_message=str(e),
            )
            return False

    async def go_back(self) -> bool:
        """Navigate back in browser history."""
        url_before = self.page.url
        try:
            await self.page.go_back(wait_until="domcontentloaded", timeout=10000)
            self._record_action(ActionType.GO_BACK, "browser back", url_before=url_before, url_after=self.page.url, success=True)
            return True
        except Exception as e:
            logger.debug(f"Go back failed: {e}")
            return False

    # ─────────────────────────────────────────────────────────────────────────
    # Interactions
    # ─────────────────────────────────────────────────────────────────────────

    async def click_element(self, element: ElementInfo) -> bool:
        """Click an element identified by its ElementInfo."""
        url_before = self.page.url
        try:
            if element.selector:
                await self.page.click(element.selector, timeout=5000, force=False)
            elif element.bounding_box:
                bb = element.bounding_box
                x = bb["x"] + bb["width"] / 2
                y = bb["y"] + bb["height"] / 2
                await self.page.mouse.click(x, y)
            else:
                # Try by text as last resort
                if element.text:
                    await self.page.get_by_text(element.text, exact=False).first.click(timeout=5000)
                elif element.aria_label:
                    await self.page.get_by_label(element.aria_label).first.click(timeout=5000)
                else:
                    return False

            await asyncio.sleep(0.3)
            self._record_action(
                ActionType.CLICK,
                target_description=element.display_name,
                selector=element.selector,
                url_before=url_before,
                url_after=self.page.url,
                success=True,
            )
            return True
        except Exception as e:
            logger.debug(f"Click failed on '{element.display_name}': {e}")
            self._record_action(
                ActionType.CLICK,
                target_description=element.display_name,
                selector=element.selector,
                url_before=url_before,
                success=False,
                error_message=str(e),
            )
            return False

    async def type_into(self, element: ElementInfo, text: str) -> bool:
        """Type text into an input element."""
        url_before = self.page.url
        try:
            if element.selector:
                await self.page.fill(element.selector, text, timeout=5000)
            elif element.bounding_box:
                bb = element.bounding_box
                await self.page.mouse.click(bb["x"] + bb["width"] / 2, bb["y"] + bb["height"] / 2)
                await self.page.keyboard.type(text)
            else:
                return False

            self._record_action(
                ActionType.TYPE,
                target_description=element.display_name,
                selector=element.selector,
                value=text[:50],  # truncate for privacy
                url_before=url_before,
                url_after=self.page.url,
                success=True,
            )
            return True
        except Exception as e:
            logger.debug(f"Type failed on '{element.display_name}': {e}")
            return False

    async def scroll_down(self, amount: int = 500) -> None:
        """Scroll the page down."""
        url_before = self.page.url
        await self.page.evaluate(f"window.scrollBy(0, {amount})")
        await asyncio.sleep(0.2)
        self._record_action(
            ActionType.SCROLL,
            target_description="page",
            value=f"down {amount}px",
            url_before=url_before,
            url_after=self.page.url,
            success=True,
        )

    async def scroll_to_top(self) -> None:
        await self.page.evaluate("window.scrollTo(0, 0)")

    async def press_key(self, key: str) -> None:
        """Press a keyboard key (e.g. 'Escape', 'Tab', 'Enter')."""
        url_before = self.page.url
        await self.page.keyboard.press(key)
        self._record_action(
            ActionType.PRESS,
            target_description="keyboard",
            value=key,
            url_before=url_before,
            url_after=self.page.url,
            success=True,
        )

    async def close_dialog(self) -> None:
        """Attempt to close any visible dialog/modal."""
        url_before = self.page.url
        # Try pressing Escape first
        await self.page.keyboard.press("Escape")
        await asyncio.sleep(0.2)
        # Then try clicking common close button patterns
        close_selectors = [
            '[aria-label="Close"]',
            '[aria-label="close"]',
            'button:has-text("Close")',
            'button:has-text("×")',
            '.modal-close',
            '.close-button',
        ]
        for sel in close_selectors:
            try:
                el = await self.page.query_selector(sel)
                if el and await el.is_visible():
                    await el.click()
                    break
            except Exception:
                pass
        self._record_action(ActionType.CLOSE_DIALOG, "dialog", url_before=url_before, success=True)

    # ─────────────────────────────────────────────────────────────────────────
    # State capture
    # ─────────────────────────────────────────────────────────────────────────

    async def capture_screenshot(self, filename: str | None = None, full_page: bool = False) -> str | None:
        """
        Capture a screenshot and save to the screenshots directory.
        Returns the saved file path, or None on failure.
        """
        self.screenshots_dir.mkdir(parents=True, exist_ok=True)
        if not filename:
            ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
            safe_url = self.page.url.replace("https://", "").replace("http://", "").replace("/", "_")[:40]
            filename = f"{ts}_{safe_url}.png"

        path = self.screenshots_dir / filename
        try:
            await self.page.screenshot(
                path=str(path),
                full_page=full_page,
                timeout=10000,
            )
            return str(path)
        except Exception as e:
            logger.warning(f"Screenshot failed: {e}")
            return None

    async def capture_screenshot_b64(self) -> str | None:
        """Return screenshot as base64 string (for VLM)."""
        try:
            data = await self.page.screenshot(full_page=False, timeout=10000)
            return base64.b64encode(data).decode("utf-8")
        except Exception as e:
            logger.warning(f"Screenshot (b64) failed: {e}")
            return None

    async def get_page_title(self) -> str:
        try:
            return await self.page.title()
        except Exception:
            return ""

    async def get_page_content_hash(self) -> str:
        """Content-based hash for state deduplication."""
        try:
            content = await self.page.evaluate("document.body.innerText")
            return hashlib.md5(f"{self.page.url}|{content[:2000]}".encode()).hexdigest()
        except Exception:
            return hashlib.md5(self.page.url.encode()).hexdigest()

    async def get_interactive_elements(self) -> list[ElementInfo]:
        """Discover interactive elements via JavaScript DOM scan."""
        try:
            raw = await self.page.evaluate(_JS_GET_INTERACTIVE_ELEMENTS)
            elements = []
            for item in raw:
                elements.append(
                    ElementInfo(
                        tag=item.get("tag", ""),
                        role=item.get("role"),
                        text=item.get("text") or None,
                        aria_label=item.get("aria_label"),
                        placeholder=item.get("placeholder"),
                        href=item.get("href"),
                        input_type=item.get("input_type"),
                        bounding_box=item.get("bounding_box"),
                        is_visible=True,
                        is_enabled=True,
                    )
                )
            return elements
        except Exception as e:
            logger.debug(f"Failed to get interactive elements: {e}")
            return []

    async def get_images(self) -> list[dict]:
        """Return all <img> elements with status info."""
        try:
            return await self.page.evaluate(_JS_GET_IMAGES)
        except Exception:
            return []

    async def get_links(self) -> list[dict]:
        """Return all anchor links from the page."""
        try:
            return await self.page.evaluate(_JS_GET_LINKS)
        except Exception:
            return []

    async def get_dom_metrics(self) -> dict[str, Any]:
        """Return DOM layout metrics (scroll sizes, overflow info)."""
        try:
            return await self.page.evaluate(_JS_GET_DOM_METRICS)
        except Exception:
            return {}

    async def get_overflow_elements(self) -> list[dict]:
        """Return elements that appear to overflow outside the viewport."""
        try:
            return await self.page.evaluate(_JS_GET_OVERFLOW_ELEMENTS)
        except Exception:
            return []

    async def capture_full_state(
        self, viewport_label: str, depth: int, parent_url: str | None, screenshot_filename: str | None = None
    ) -> PageState:
        """
        Capture a complete PageState snapshot.
        This is called after every navigation or significant action.
        """
        title = await self.get_page_title()
        screenshot_path = await self.capture_screenshot(screenshot_filename)
        elements = await self.get_interactive_elements()
        dom_metrics = await self.get_dom_metrics()
        page_hash = await self.get_page_content_hash()

        return PageState(
            url=self.page.url,
            title=title,
            screenshot_path=screenshot_path,
            visible_elements=elements,
            console_messages=list(self._console_messages),
            network_events=list(self._network_events),
            viewport=viewport_label,
            depth=depth,
            parent_url=parent_url,
            actions_performed=list(self._action_history),
            page_hash=page_hash,
            dom_metrics=dom_metrics,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Accessibility
    # ─────────────────────────────────────────────────────────────────────────

    async def run_axe(self, tags: list[str] | None = None) -> dict:
        """
        Inject and run axe-core accessibility scanner.
        Returns the full axe results dict.
        """
        axe_tags = tags or ["wcag2a", "wcag2aa", "best-practice"]

        # Inject axe-core from CDN
        try:
            await self.page.add_script_tag(
                url="https://cdn.jsdelivr.net/npm/axe-core@4.10.0/axe.min.js"
            )
            await asyncio.sleep(0.5)

            results = await self.page.evaluate(
                f"""
                async () => {{
                    return await new Promise((resolve) => {{
                        axe.run({{ runOnly: {{ type: 'tag', values: {json.dumps(axe_tags)} }} }},
                            (err, results) => resolve(err ? {{error: err.toString()}} : results));
                    }});
                }}
                """
            )
            return results or {}
        except Exception as e:
            logger.warning(f"axe-core run failed: {e}")
            return {"error": str(e)}

    # ─────────────────────────────────────────────────────────────────────────
    # Internal helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _record_action(
        self,
        action_type: ActionType,
        target_description: str,
        selector: str | None = None,
        value: str | None = None,
        url_before: str = "",
        url_after: str | None = None,
        success: bool = True,
        error_message: str | None = None,
    ) -> None:
        record = ActionRecord(
            action_type=action_type,
            target_description=target_description,
            selector=selector,
            value=value,
            url_before=url_before,
            url_after=url_after,
            success=success,
            error_message=error_message,
        )
        self._action_history.append(record)

    def clear_events(self) -> None:
        """Clear collected events (call between page visits if desired)."""
        self._console_messages.clear()
        self._network_events.clear()

    @property
    def console_messages(self) -> list[ConsoleMessage]:
        return list(self._console_messages)

    @property
    def network_events(self) -> list[NetworkEvent]:
        return list(self._network_events)

    @property
    def action_history(self) -> list[ActionRecord]:
        return list(self._action_history)

    @property
    def current_url(self) -> str:
        try:
            return self.page.url
        except Exception:
            return ""
