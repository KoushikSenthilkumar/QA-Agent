"""
Integration tests — run validators against real browser sessions.

Requires: playwright install chromium

These tests use the intentionally broken test sites in examples/test-sites/.
They serve the HTML files via a simple HTTP server started as a fixture.
"""

import asyncio
import functools
import http.server
import threading
from pathlib import Path

import pytest


def _playwright_available() -> bool:
    try:
        from playwright.async_api import async_playwright  # noqa: F401
        return True
    except ImportError:
        return False


pytestmark = pytest.mark.skipif(
    not _playwright_available(),
    reason="Playwright not available",
)


@pytest.fixture(scope="module")
def test_server():
    """Serve test sites on localhost:18080."""
    test_sites_dir = Path(__file__).parent.parent.parent / "examples" / "test-sites"
    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler,
        directory=str(test_sites_dir),
    )
    server = http.server.HTTPServer(("localhost", 18080), handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.daemon = True
    thread.start()
    yield "http://localhost:18080"
    server.shutdown()


@pytest.fixture
async def browser_page():
    """Provide a Playwright page for testing."""
    from playwright.async_api import async_playwright
    from qa_agent.browser.page_controller import PageController
    from pathlib import Path
    import tempfile

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1366, "height": 768}
        )
        page = await context.new_page()
        with tempfile.TemporaryDirectory() as tmpdir:
            controller = PageController(page=page, screenshots_dir=Path(tmpdir))
            controller.start_monitoring()
            yield controller
            controller.stop_monitoring()
        await context.close()
        await browser.close()


@pytest.mark.asyncio
async def test_console_error_detection(test_server, browser_page):
    """Console errors on the broken site should be detected."""
    from qa_agent.validators import ConsoleValidator

    url = f"{test_server}/console-error-site/index.html"
    await browser_page.navigate(url)
    await asyncio.sleep(1.5)  # Wait for JS errors to fire

    page_state = await browser_page.capture_full_state(
        viewport_label="1366x768",
        depth=0,
        parent_url=None,
    )

    validator = ConsoleValidator()
    findings = await validator.validate(page_state)

    assert len(findings) > 0, "Should detect console errors on the broken site"
    assert all(f.category.value == "CONSOLE" for f in findings)


@pytest.mark.asyncio
async def test_image_broken_detection(test_server, browser_page):
    """Broken images should be detected."""
    from qa_agent.validators import ImageValidator

    url = f"{test_server}/broken-image-site/index.html"
    await browser_page.navigate(url)
    await asyncio.sleep(1.0)

    page_state = await browser_page.capture_full_state(
        viewport_label="1366x768",
        depth=0,
        parent_url=None,
    )
    images = await browser_page.get_images()

    validator = ImageValidator()
    findings = await validator.validate(page_state, images=images)

    assert len(findings) > 0, "Should detect broken images"
    image_findings = [f for f in findings if f.category.value == "IMAGE"]
    assert len(image_findings) > 0


@pytest.mark.asyncio
async def test_overflow_detection(test_server, browser_page):
    """Horizontal overflow should be detected on the overflow test site."""
    from qa_agent.validators import LayoutValidator

    url = f"{test_server}/overflow-site/index.html"
    await browser_page.navigate(url)
    await asyncio.sleep(0.5)

    page_state = await browser_page.capture_full_state(
        viewport_label="1366x768",
        depth=0,
        parent_url=None,
    )
    overflow_elements = await browser_page.get_overflow_elements()

    validator = LayoutValidator()
    findings = await validator.validate(page_state, overflow_elements=overflow_elements)

    # The overflow site has a 1200px fixed-width container — should trigger
    layout_findings = [f for f in findings if f.category.value == "LAYOUT"]
    assert len(layout_findings) > 0, "Should detect layout overflow"
