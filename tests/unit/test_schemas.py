"""
Unit tests for data schemas.
Tests that our Pydantic models behave correctly.
"""

from qa_agent.utils.schemas import (
    ActionRecord,
    ActionType,
    ConsoleMessage,
    ElementInfo,
    Finding,
    NetworkEvent,
    QARunResult,
    Severity,
    Category,
    ViewportConfig,
)


class TestSeverity:
    def test_order_critical_is_lowest(self):
        assert Severity.CRITICAL.order < Severity.HIGH.order

    def test_order_info_is_highest(self):
        assert Severity.INFO.order > Severity.LOW.order

    def test_sorting(self):
        severities = [Severity.LOW, Severity.CRITICAL, Severity.INFO, Severity.HIGH]
        sorted_sev = sorted(severities, key=lambda s: s.order)
        assert sorted_sev[0] == Severity.CRITICAL
        assert sorted_sev[-1] == Severity.INFO


class TestElementInfo:
    def test_display_name_uses_aria_label(self):
        el = ElementInfo(tag="button", aria_label="Submit form", text="Submit")
        assert el.display_name == "Submit form"

    def test_display_name_falls_back_to_text(self):
        el = ElementInfo(tag="a", text="Click here")
        assert el.display_name == "Click here"

    def test_display_name_falls_back_to_tag(self):
        el = ElementInfo(tag="div")
        assert el.display_name == "div"


class TestConsoleMessage:
    def test_is_error_for_error_level(self):
        msg = ConsoleMessage(level="error", text="Something broke")
        assert msg.is_error is True

    def test_is_not_error_for_info(self):
        msg = ConsoleMessage(level="info", text="App initialized")
        assert msg.is_error is False

    def test_is_error_for_assert(self):
        msg = ConsoleMessage(level="assert", text="Assertion failed")
        assert msg.is_error is True


class TestNetworkEvent:
    def test_is_failure_for_404(self):
        ev = NetworkEvent(url="https://example.com/missing", status=404)
        assert ev.is_failure is True

    def test_is_failure_for_500(self):
        ev = NetworkEvent(url="https://example.com/error", status=500)
        assert ev.is_failure is True
        assert ev.is_server_error is True

    def test_is_not_failure_for_200(self):
        ev = NetworkEvent(url="https://example.com/ok", status=200)
        assert ev.is_failure is False

    def test_is_failure_for_request_error(self):
        ev = NetworkEvent(url="https://broken.com/", error="net::ERR_NAME_NOT_RESOLVED")
        assert ev.is_failure is True


class TestActionRecord:
    def test_click_human_step(self):
        action = ActionRecord(
            action_type=ActionType.CLICK,
            target_description="Login button",
            url_before="https://example.com",
        )
        assert "Login button" in action.to_human_step()
        assert "Click" in action.to_human_step()

    def test_navigate_human_step(self):
        action = ActionRecord(
            action_type=ActionType.NAVIGATE,
            target_description="https://example.com",
            url_before="about:blank",
        )
        step = action.to_human_step()
        assert "Navigate" in step

    def test_type_human_step_includes_value(self):
        action = ActionRecord(
            action_type=ActionType.TYPE,
            target_description="Search box",
            value="test query",
            url_before="https://example.com",
        )
        step = action.to_human_step()
        assert "test query" in step


class TestFinding:
    def test_confidence_clamped(self):
        f = Finding(
            title="Test",
            severity=Severity.HIGH,
            category=Category.CONSOLE,
            url="https://example.com",
            description="Test finding",
            confidence=1.5,  # Should be clamped to 1.0
        )
        assert f.confidence == 1.0

    def test_confidence_clamped_negative(self):
        f = Finding(
            title="Test",
            severity=Severity.LOW,
            category=Category.NETWORK,
            url="https://example.com",
            description="Test finding",
            confidence=-0.5,  # Should be clamped to 0.0
        )
        assert f.confidence == 0.0

    def test_default_id_format(self):
        f = Finding(
            title="Test",
            severity=Severity.MEDIUM,
            category=Category.IMAGE,
            url="https://example.com",
            description="A test finding",
        )
        assert f.id.startswith("BUG-")

    def test_to_dict_serializable(self):
        import json
        f = Finding(
            title="Test",
            severity=Severity.INFO,
            category=Category.LAYOUT,
            url="https://example.com",
            description="Test",
        )
        d = f.to_dict()
        # Should be JSON serializable
        json.dumps(d)


class TestViewportConfig:
    def test_str_representation(self):
        vp = ViewportConfig(name="Desktop", width=1366, height=768)
        assert str(vp) == "1366x768"

    def test_label_includes_name(self):
        vp = ViewportConfig(name="Mobile", width=390, height=844)
        assert "Mobile" in vp.label
        assert "390" in vp.label


class TestQARunResult:
    def test_severity_counts(self):
        result = QARunResult(target_url="https://example.com")
        result.findings = [
            Finding(title="c1", severity=Severity.CRITICAL, category=Category.CONSOLE, url="x", description="x"),
            Finding(title="h1", severity=Severity.HIGH, category=Category.NETWORK, url="x", description="x"),
            Finding(title="h2", severity=Severity.HIGH, category=Category.LAYOUT, url="x", description="x"),
            Finding(title="m1", severity=Severity.MEDIUM, category=Category.IMAGE, url="x", description="x"),
        ]
        assert result.critical_count == 1
        assert result.high_count == 2
        assert result.medium_count == 1
        assert result.low_count == 0
        assert result.total_issues == 4
