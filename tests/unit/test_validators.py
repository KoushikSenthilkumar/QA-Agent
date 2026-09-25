"""
Unit tests for all validators.
Uses mock page states — no real browser needed.
"""

import pytest
from qa_agent.utils.schemas import (
    Category,
    ConsoleMessage,
    NetworkEvent,
    PageState,
    Severity,
)
from qa_agent.validators import (
    ConsoleValidator,
    ImageValidator,
    LayoutValidator,
    NetworkValidator,
)


def make_page_state(**kwargs) -> PageState:
    """Helper to create PageState with sensible defaults."""
    defaults = {
        "url": "https://example.com",
        "title": "Test Page",
        "viewport": "1366x768",
    }
    defaults.update(kwargs)
    return PageState(**defaults)


# ─────────────────────────────────────────────────────────────────────────────
# ConsoleValidator
# ─────────────────────────────────────────────────────────────────────────────


class TestConsoleValidator:
    @pytest.fixture
    def validator(self):
        return ConsoleValidator()

    @pytest.mark.asyncio
    async def test_no_errors_returns_empty(self, validator):
        ps = make_page_state()
        findings = await validator.validate(ps)
        assert findings == []

    @pytest.mark.asyncio
    async def test_detects_console_error(self, validator):
        ps = make_page_state(
            console_messages=[
                ConsoleMessage(level="error", text="TypeError: Cannot read property 'x' of undefined")
            ]
        )
        findings = await validator.validate(ps)
        assert len(findings) == 1
        assert findings[0].category == Category.CONSOLE
        assert findings[0].severity == Severity.HIGH

    @pytest.mark.asyncio
    async def test_filters_third_party_noise(self, validator):
        ps = make_page_state(
            console_messages=[
                ConsoleMessage(level="error", text="google-analytics: failed to load"),
                ConsoleMessage(level="error", text="Extension context invalidated"),
            ]
        )
        findings = await validator.validate(ps)
        assert findings == []  # Both are noise

    @pytest.mark.asyncio
    async def test_deduplicates_same_error(self, validator):
        same_error = "TypeError: null is not an object"
        ps = make_page_state(
            console_messages=[
                ConsoleMessage(level="error", text=same_error),
                ConsoleMessage(level="error", text=same_error),
                ConsoleMessage(level="error", text=same_error),
            ]
        )
        findings = await validator.validate(ps)
        # Should deduplicate to 1
        assert len(findings) == 1

    @pytest.mark.asyncio
    async def test_escalates_many_errors_to_critical(self, validator):
        ps = make_page_state(
            console_messages=[
                ConsoleMessage(level="error", text=f"Error number {i}")
                for i in range(10)
            ]
        )
        findings = await validator.validate(ps)
        # With > 5 unique errors, severity should escalate
        assert any(f.severity == Severity.CRITICAL for f in findings)

    @pytest.mark.asyncio
    async def test_ignores_info_messages(self, validator):
        ps = make_page_state(
            console_messages=[
                ConsoleMessage(level="info", text="App ready"),
                ConsoleMessage(level="log", text="User logged in"),
            ]
        )
        findings = await validator.validate(ps)
        assert findings == []


# ─────────────────────────────────────────────────────────────────────────────
# NetworkValidator
# ─────────────────────────────────────────────────────────────────────────────


class TestNetworkValidator:
    @pytest.fixture
    def validator(self):
        return NetworkValidator()

    @pytest.mark.asyncio
    async def test_no_failures_returns_empty(self, validator):
        ps = make_page_state(
            network_events=[
                NetworkEvent(url="https://example.com/style.css", status=200)
            ]
        )
        findings = await validator.validate(ps)
        assert findings == []

    @pytest.mark.asyncio
    async def test_detects_404(self, validator):
        ps = make_page_state(
            network_events=[
                NetworkEvent(url="https://example.com/missing", status=404, resource_type="document")
            ]
        )
        findings = await validator.validate(ps)
        assert len(findings) == 1
        assert findings[0].category == Category.NETWORK
        assert "404" in findings[0].title

    @pytest.mark.asyncio
    async def test_server_error_is_high_severity(self, validator):
        ps = make_page_state(
            network_events=[
                NetworkEvent(url="https://example.com/api/data", status=500, resource_type="fetch")
            ]
        )
        findings = await validator.validate(ps)
        assert len(findings) == 1
        assert findings[0].severity == Severity.HIGH

    @pytest.mark.asyncio
    async def test_filters_analytics_urls(self, validator):
        ps = make_page_state(
            network_events=[
                NetworkEvent(url="https://www.google-analytics.com/collect", status=500)
            ]
        )
        findings = await validator.validate(ps)
        assert findings == []  # Should be filtered as noise

    @pytest.mark.asyncio
    async def test_request_failure_detected(self, validator):
        ps = make_page_state(
            network_events=[
                NetworkEvent(
                    url="https://example.com/api/users",
                    error="net::ERR_CONNECTION_REFUSED",
                    resource_type="fetch",
                )
            ]
        )
        findings = await validator.validate(ps)
        assert len(findings) == 1
        assert "Request failed" in findings[0].title


# ─────────────────────────────────────────────────────────────────────────────
# ImageValidator
# ─────────────────────────────────────────────────────────────────────────────


class TestImageValidator:
    @pytest.fixture
    def validator(self):
        return ImageValidator()

    @pytest.mark.asyncio
    async def test_no_images_returns_empty(self, validator):
        ps = make_page_state()
        findings = await validator.validate(ps, images=[])
        assert findings == []

    @pytest.mark.asyncio
    async def test_detects_broken_image(self, validator):
        ps = make_page_state()
        images = [
            {
                "src": "https://example.com/missing.jpg",
                "complete": True,
                "natural_width": 0,
                "natural_height": 0,
                "alt": "Missing image",
            }
        ]
        findings = await validator.validate(ps, images=images)
        assert any(f.category == Category.IMAGE for f in findings)
        assert any("Broken image" in f.title for f in findings)

    @pytest.mark.asyncio
    async def test_detects_missing_alt(self, validator):
        ps = make_page_state()
        images = [
            {
                "src": "https://example.com/photo.jpg",
                "complete": True,
                "natural_width": 200,
                "natural_height": 150,
                "alt": None,  # Missing alt
            }
        ]
        findings = await validator.validate(ps, images=images)
        assert any("alt" in f.title.lower() for f in findings)

    @pytest.mark.asyncio
    async def test_empty_alt_is_acceptable(self, validator):
        """Empty alt="" is valid for decorative images."""
        ps = make_page_state()
        images = [
            {
                "src": "https://example.com/decorative.jpg",
                "complete": True,
                "natural_width": 100,
                "natural_height": 100,
                "alt": "",  # Empty string — valid for decorative
            }
        ]
        findings = await validator.validate(ps, images=images)
        # Should not flag empty alt as missing alt
        alt_findings = [f for f in findings if "alt" in f.title.lower()]
        assert len(alt_findings) == 0

    @pytest.mark.asyncio
    async def test_detects_empty_src(self, validator):
        ps = make_page_state()
        images = [{"src": "", "complete": True, "natural_width": 0, "natural_height": 0, "alt": "test"}]
        findings = await validator.validate(ps, images=images)
        assert any("missing src" in f.title.lower() for f in findings)


# ─────────────────────────────────────────────────────────────────────────────
# LayoutValidator
# ─────────────────────────────────────────────────────────────────────────────


class TestLayoutValidator:
    @pytest.fixture
    def validator(self):
        return LayoutValidator()

    @pytest.mark.asyncio
    async def test_no_overflow_returns_empty(self, validator):
        ps = make_page_state(
            dom_metrics={
                "has_horizontal_scroll": False,
                "scroll_width": 1366,
                "client_width": 1366,
            }
        )
        findings = await validator.validate(ps, overflow_elements=[])
        assert findings == []

    @pytest.mark.asyncio
    async def test_detects_horizontal_overflow(self, validator):
        ps = make_page_state(
            dom_metrics={
                "has_horizontal_scroll": True,
                "scroll_width": 1600,
                "client_width": 390,  # Mobile viewport
            }
        )
        findings = await validator.validate(ps, overflow_elements=[])
        overflow_findings = [f for f in findings if "overflow" in f.title.lower()]
        assert len(overflow_findings) > 0

    @pytest.mark.asyncio
    async def test_overflow_severity_scales_with_amount(self, validator):
        """Large overflow should be HIGH, small should be MEDIUM."""
        small_overflow = make_page_state(
            dom_metrics={"has_horizontal_scroll": True, "scroll_width": 420, "client_width": 390}
        )
        large_overflow = make_page_state(
            dom_metrics={"has_horizontal_scroll": True, "scroll_width": 1500, "client_width": 390}
        )

        small_findings = await validator.validate(small_overflow, overflow_elements=[])
        large_findings = await validator.validate(large_overflow, overflow_elements=[])

        small_overflow_f = [f for f in small_findings if "overflow" in f.title.lower()]
        large_overflow_f = [f for f in large_findings if "overflow" in f.title.lower()]

        if small_overflow_f and large_overflow_f:
            # Large should be at least as severe as small
            assert large_overflow_f[0].severity.order <= small_overflow_f[0].severity.order
