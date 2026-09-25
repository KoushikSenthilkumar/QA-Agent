"""
Configuration loading and validation.

Config is loaded from config.yaml (or a user-specified file),
then individual fields can be overridden by CLI arguments or environment variables.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator

from qa_agent.utils.schemas import ViewportConfig


# ─────────────────────────────────────────────────────────────────────────────
# Config sub-models
# ─────────────────────────────────────────────────────────────────────────────


class ExplorationConfig(BaseModel):
    max_pages: int = 20
    max_actions: int = 100
    max_depth: int = 5
    timeout: int = 300
    same_domain_only: bool = True


class ValidatorsConfig(BaseModel):
    console: bool = True
    network: bool = True
    links: bool = True
    images: bool = True
    layout: bool = True
    accessibility: bool = True
    responsive: bool = True
    visual_ai: bool = True


class VisionConfig(BaseModel):
    provider: str = "mock"  # "openai" | "mock"
    model: str = "gpt-4o"
    max_tokens: int = 2048
    temperature: float = 0.1
    timeout: int = 30


class RecordingConfig(BaseModel):
    enabled: bool = True
    format: str = "webm"


class OutputConfig(BaseModel):
    reports_dir: str = "./reports"
    screenshots_dir: str = "./screenshots"
    recordings_dir: str = "./recordings"
    logs_dir: str = "./logs"
    baselines_dir: str = "./baselines"
    report_formats: list[str] = Field(default_factory=lambda: ["json", "html"])


class SafetyConfig(BaseModel):
    skip_destructive_actions: bool = True
    destructive_keywords: list[str] = Field(
        default_factory=lambda: [
            "delete",
            "remove",
            "purchase",
            "buy now",
            "checkout",
            "pay",
            "send",
            "reset password",
            "cancel account",
        ]
    )


class AccessibilityConfig(BaseModel):
    run_axe: bool = True
    axe_tags: list[str] = Field(default_factory=lambda: ["wcag2a", "wcag2aa", "best-practice"])


# ─────────────────────────────────────────────────────────────────────────────
# Main config model
# ─────────────────────────────────────────────────────────────────────────────


class QAConfig(BaseModel):
    """Complete QA Agent configuration."""

    target_url: str = ""

    exploration: ExplorationConfig = Field(default_factory=ExplorationConfig)
    viewports: list[ViewportConfig] = Field(
        default_factory=lambda: [
            ViewportConfig(name="Desktop", width=1366, height=768),
            ViewportConfig(name="Laptop", width=1280, height=720),
            ViewportConfig(name="Tablet", width=768, height=1024),
            ViewportConfig(name="Mobile", width=390, height=844),
        ]
    )
    validators: ValidatorsConfig = Field(default_factory=ValidatorsConfig)
    vision: VisionConfig = Field(default_factory=VisionConfig)
    recording: RecordingConfig = Field(default_factory=RecordingConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)
    safety: SafetyConfig = Field(default_factory=SafetyConfig)
    accessibility: AccessibilityConfig = Field(default_factory=AccessibilityConfig)
    browser_type: str = "chromium"
    headless: bool = True

    @field_validator("target_url")
    @classmethod
    def normalize_url(cls, v: str) -> str:
        if v and not v.startswith(("http://", "https://")):
            return "https://" + v
        return v

    def apply_env_overrides(self) -> None:
        """Override config values from environment variables."""
        provider = os.getenv("VLM_PROVIDER")
        if provider:
            self.vision.provider = provider

        api_model = os.getenv("VLM_MODEL")
        if api_model:
            self.vision.model = api_model

        browser = os.getenv("BROWSER_TYPE")
        if browser:
            self.browser_type = browser

        headless_env = os.getenv("HEADLESS")
        if headless_env is not None:
            self.headless = headless_env.lower() in ("true", "1", "yes")

    def get_output_dirs(self, run_id: str) -> dict[str, Path]:
        """Return absolute paths for this run's output directories."""
        base = {
            "reports": Path(self.output.reports_dir) / run_id,
            "screenshots": Path(self.output.screenshots_dir) / run_id,
            "recordings": Path(self.output.recordings_dir) / run_id,
            "logs": Path(self.output.logs_dir) / run_id,
        }
        return base


# ─────────────────────────────────────────────────────────────────────────────
# Loader
# ─────────────────────────────────────────────────────────────────────────────


def load_config(config_path: str | None = None, overrides: dict[str, Any] | None = None) -> QAConfig:
    """
    Load configuration from a YAML file.

    Priority: env vars > CLI overrides > config file > defaults.

    Args:
        config_path: Path to YAML config file. Defaults to ./config.yaml.
        overrides: Dictionary of values to override (typically from CLI args).
    """
    # Find the config file
    if config_path is None:
        # Try workspace-relative config.yaml first
        candidates = [
            Path("config.yaml"),
            Path(__file__).parent.parent.parent.parent / "config.yaml",
        ]
        for candidate in candidates:
            if candidate.exists():
                config_path = str(candidate)
                break

    raw: dict[str, Any] = {}
    if config_path and Path(config_path).exists():
        with open(config_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

    # Flatten the YAML structure into what QAConfig expects
    flat: dict[str, Any] = {}

    target = raw.get("target", {})
    if isinstance(target, dict):
        flat["target_url"] = target.get("url", "")

    if "exploration" in raw:
        flat["exploration"] = raw["exploration"]

    if "viewports" in raw:
        flat["viewports"] = raw["viewports"]

    if "validators" in raw:
        flat["validators"] = raw["validators"]

    if "vision" in raw:
        flat["vision"] = raw["vision"]

    if "recording" in raw:
        flat["recording"] = raw["recording"]

    if "output" in raw:
        flat["output"] = raw["output"]

    if "safety" in raw:
        flat["safety"] = raw["safety"]

    if "accessibility" in raw:
        flat["accessibility"] = raw["accessibility"]

    # Apply CLI overrides
    if overrides:
        for key, value in overrides.items():
            if value is not None:
                flat[key] = value

    config = QAConfig(**flat)
    config.apply_env_overrides()
    return config
