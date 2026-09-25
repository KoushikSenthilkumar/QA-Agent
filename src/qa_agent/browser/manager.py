"""
BrowserManager — Playwright lifecycle management.

Responsible for:
- Starting and stopping the Playwright browser
- Creating browser contexts (one per run or per viewport)
- Providing page instances
- Managing video recording per context
- Centralizing browser-type selection

This module does NOT contain any test logic.
It is purely infrastructure for browser control.
"""

from __future__ import annotations

from pathlib import Path

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
)

from qa_agent.utils.config import QAConfig
from qa_agent.utils.logger import get_logger
from qa_agent.utils.schemas import ViewportConfig

logger = get_logger(__name__)


class BrowserManager:
    """
    Manages the Playwright browser lifecycle for a QA run.

    Usage:
        async with BrowserManager(config) as bm:
            context = await bm.new_context(viewport)
            page = await context.new_page()
            ...
    """

    def __init__(self, config: QAConfig, recordings_dir: Path | None = None):
        self.config = config
        self.recordings_dir = recordings_dir
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._contexts: list[BrowserContext] = []

    async def __aenter__(self) -> "BrowserManager":
        await self.start()
        return self

    async def __aexit__(self, *_) -> None:
        await self.stop()

    async def start(self) -> None:
        """Launch the Playwright browser."""
        logger.info(f"Launching {self.config.browser_type} browser (headless={self.config.headless})")
        self._playwright = await async_playwright().start()

        browser_launcher = getattr(self._playwright, self.config.browser_type)
        self._browser = await browser_launcher.launch(
            headless=self.config.headless,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        logger.debug(f"Browser started: {self._browser.version}")

    async def stop(self) -> None:
        """Close all contexts and the browser."""
        for ctx in self._contexts:
            try:
                await ctx.close()
            except Exception as e:
                logger.debug(f"Error closing context: {e}")
        self._contexts.clear()

        if self._browser:
            await self._browser.close()
            self._browser = None

        if self._playwright:
            await self._playwright.stop()
            self._playwright = None

        logger.debug("Browser stopped")

    async def new_context(
        self,
        viewport: ViewportConfig | None = None,
        record_video: bool = False,
        extra_http_headers: dict[str, str] | None = None,
    ) -> BrowserContext:
        """
        Create a new browser context.

        Each context is isolated (separate cookies, storage, etc.).
        Use one context per viewport or per test scenario.

        Args:
            viewport: Viewport dimensions. Defaults to desktop if None.
            record_video: Whether to record video for this context.
            extra_http_headers: Additional HTTP headers to send with all requests.
        """
        if not self._browser:
            raise RuntimeError("BrowserManager not started. Call start() or use as context manager.")

        vp = viewport or ViewportConfig(name="Desktop", width=1366, height=768)

        context_options: dict = {
            "viewport": {"width": vp.width, "height": vp.height},
            "user_agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/127.0.0.0 Safari/537.36"
            ),
            "locale": "en-US",
            "timezone_id": "America/New_York",
            "permissions": ["geolocation"],
            "ignore_https_errors": True,  # Don't fail on SSL issues during testing
        }

        if extra_http_headers:
            context_options["extra_http_headers"] = extra_http_headers

        if record_video and self.recordings_dir and self.config.recording.enabled:
            self.recordings_dir.mkdir(parents=True, exist_ok=True)
            context_options["record_video_dir"] = str(self.recordings_dir)
            context_options["record_video_size"] = {"width": vp.width, "height": vp.height}

        context = await self._browser.new_context(**context_options)
        self._contexts.append(context)

        logger.debug(f"Created browser context: {vp.label}")
        return context

    async def new_page(
        self,
        viewport: ViewportConfig | None = None,
        record_video: bool = False,
    ) -> tuple[Page, BrowserContext]:
        """
        Convenience method: create a context + page in one call.

        Returns both the page and its context (needed to close it later
        and to retrieve video path after close).
        """
        context = await self.new_context(viewport, record_video=record_video)
        page = await context.new_page()
        return page, context

    @property
    def browser(self) -> Browser:
        if not self._browser:
            raise RuntimeError("Browser not started")
        return self._browser
