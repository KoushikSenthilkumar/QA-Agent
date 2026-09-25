"""
Unit tests for vision providers.
Tests mock provider behavior without any real API calls.
"""

import pytest
from qa_agent.vision.mock_provider import MockVisionProvider
from qa_agent.vision.base import ActionPlan, VisualAnalysisResult


class TestMockVisionProvider:
    @pytest.fixture
    def provider(self):
        return MockVisionProvider()

    @pytest.fixture
    def clean_provider(self):
        return MockVisionProvider(always_clean=True)

    @pytest.fixture
    def issues_provider(self):
        return MockVisionProvider(always_find_issues=True)

    @pytest.mark.asyncio
    async def test_analyze_screenshot_returns_result(self, provider):
        result = await provider.analyze_screenshot(
            screenshot_b64="fake_base64_data",
            context={"url": "https://example.com", "viewport": "1366x768"},
        )
        assert isinstance(result, VisualAnalysisResult)
        assert result.analysis_successful is True
        assert result.provider == "mock"

    @pytest.mark.asyncio
    async def test_analyze_screenshot_has_findings(self, provider):
        result = await provider.analyze_screenshot(
            screenshot_b64="fake",
            context={"url": "https://example.com", "viewport": "1366x768"},
        )
        assert isinstance(result.findings, list)
        assert len(result.findings) > 0

    @pytest.mark.asyncio
    async def test_plan_action_returns_action_plan(self, provider):
        plan = await provider.plan_action(
            screenshot_b64="fake",
            page_context={"url": "https://example.com", "actions_taken": 0},
        )
        assert isinstance(plan, ActionPlan)
        assert plan.action in ("click", "scroll", "hover", "type", "navigate", "go_back", "done")

    @pytest.mark.asyncio
    async def test_plan_action_returns_done_after_many_actions(self, provider):
        plan = await provider.plan_action(
            screenshot_b64="fake",
            page_context={"url": "https://example.com", "actions_taken": 10},
        )
        assert plan.action == "done"
        assert plan.exploration_complete is True

    @pytest.mark.asyncio
    async def test_describe_bug_returns_string(self, provider):
        description = await provider.describe_bug(
            screenshot_b64="fake",
            finding_context={
                "category": "VISUAL",
                "url": "https://example.com",
                "viewport": "390x844",
                "title": "Button overlaps navigation",
            },
        )
        assert isinstance(description, str)
        assert len(description) > 0

    @pytest.mark.asyncio
    async def test_always_clean_mode(self, clean_provider):
        result = await clean_provider.analyze_screenshot(
            screenshot_b64="fake",
            context={"url": "https://example.com"},
        )
        # Should return INFO/no issues
        for finding in result.findings:
            assert finding.severity in ("INFO", "LOW")

    @pytest.mark.asyncio
    async def test_always_issues_mode(self, issues_provider):
        result = await issues_provider.analyze_screenshot(
            screenshot_b64="fake",
            context={"url": "https://example.com"},
        )
        # Should always have findings with MEDIUM or higher
        assert any(f.severity in ("MEDIUM", "HIGH", "CRITICAL") for f in result.findings)

    @pytest.mark.asyncio
    async def test_call_count_increments(self, provider):
        assert provider._call_count == 0
        await provider.analyze_screenshot("fake", {"url": "x"})
        await provider.analyze_screenshot("fake", {"url": "x"})
        assert provider._call_count == 2


class TestVisionProviderFactory:
    def test_mock_provider_when_no_key(self):
        """Factory should return mock if no API key is set."""
        import os
        from qa_agent.vision.factory import create_vision_provider
        from qa_agent.utils.config import load_config
        from qa_agent.vision.mock_provider import MockVisionProvider

        config = load_config()
        config.vision.provider = "openai"

        # Temporarily remove API key
        original = os.environ.pop("VLM_API_KEY", None)
        original_oai = os.environ.pop("OPENAI_API_KEY", None)
        try:
            provider = create_vision_provider(config)
            assert isinstance(provider, MockVisionProvider)
        finally:
            if original:
                os.environ["VLM_API_KEY"] = original
            if original_oai:
                os.environ["OPENAI_API_KEY"] = original_oai

    def test_mock_provider_explicit(self):
        """Explicit mock config should return MockVisionProvider."""
        from qa_agent.vision.factory import create_vision_provider
        from qa_agent.utils.config import load_config
        from qa_agent.vision.mock_provider import MockVisionProvider

        config = load_config()
        config.vision.provider = "mock"
        provider = create_vision_provider(config)
        assert isinstance(provider, MockVisionProvider)
