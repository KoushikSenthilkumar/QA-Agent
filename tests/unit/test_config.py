"""
Unit tests for configuration loading.
"""

from qa_agent.utils.config import load_config, QAConfig


class TestLoadConfig:
    def test_loads_defaults_without_file(self):
        config = load_config(config_path="/nonexistent/config.yaml")
        assert isinstance(config, QAConfig)
        assert config.exploration.max_pages == 20
        assert config.exploration.max_actions == 100

    def test_default_viewports(self):
        config = load_config(config_path="/nonexistent/config.yaml")
        assert len(config.viewports) == 4
        viewport_widths = [v.width for v in config.viewports]
        assert 1366 in viewport_widths
        assert 390 in viewport_widths

    def test_url_normalization(self):
        # URL normalization runs at construction time via Pydantic validator
        config = QAConfig(target_url="example.com")
        assert config.target_url == "https://example.com"

    def test_url_already_https(self):
        config = load_config(config_path="/nonexistent/config.yaml")
        config.target_url = "https://example.com"
        assert config.target_url == "https://example.com"

    def test_url_http_preserved(self):
        config = load_config(config_path="/nonexistent/config.yaml")
        config.target_url = "http://localhost:3000"
        assert config.target_url == "http://localhost:3000"

    def test_apply_env_override_mock(self, monkeypatch):
        monkeypatch.setenv("VLM_PROVIDER", "mock")
        config = load_config(config_path="/nonexistent/config.yaml")
        assert config.vision.provider == "mock"

    def test_safety_config_has_destructive_keywords(self):
        config = load_config(config_path="/nonexistent/config.yaml")
        assert "delete" in config.safety.destructive_keywords
        assert "purchase" in config.safety.destructive_keywords

    def test_all_validators_enabled_by_default(self):
        config = load_config(config_path="/nonexistent/config.yaml")
        assert config.validators.console is True
        assert config.validators.network is True
        assert config.validators.visual_ai is True
        assert config.validators.accessibility is True
