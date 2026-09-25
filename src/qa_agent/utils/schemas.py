"""
Core data schemas for the QA Agent.

All major data structures are defined here as Pydantic models.
Using Pydantic gives us free JSON serialization, validation, and type safety.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


# ─────────────────────────────────────────────────────────────────────────────
# Enumerations
# ─────────────────────────────────────────────────────────────────────────────


class Severity(str, Enum):
    """Bug severity levels — see ARCHITECTURE.md §10 for assignment rules."""

    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INFO = "INFO"

    @property
    def order(self) -> int:
        """Numeric order for sorting (lower = more severe)."""
        return {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "INFO": 4}[self.value]


class Category(str, Enum):
    """Bug category — determines which validator produced the finding."""

    VISUAL = "VISUAL"
    CONSOLE = "CONSOLE"
    NETWORK = "NETWORK"
    LAYOUT = "LAYOUT"
    ACCESSIBILITY = "ACCESSIBILITY"
    LINK = "LINK"
    IMAGE = "IMAGE"
    RESPONSIVE = "RESPONSIVE"
    PERFORMANCE = "PERFORMANCE"
    CRASH = "CRASH"


class ActionType(str, Enum):
    """Browser actions the exploration engine can execute."""

    CLICK = "click"
    TYPE = "type"
    SELECT = "select"
    SCROLL = "scroll"
    HOVER = "hover"
    PRESS = "press"
    NAVIGATE = "navigate"
    GO_BACK = "go_back"
    REFRESH = "refresh"
    CLOSE_DIALOG = "close_dialog"
    RESIZE = "resize"


# ─────────────────────────────────────────────────────────────────────────────
# Supporting models
# ─────────────────────────────────────────────────────────────────────────────


class ElementInfo(BaseModel):
    """Describes a discovered interactive element on the page."""

    tag: str
    role: str | None = None
    text: str | None = None
    placeholder: str | None = None
    aria_label: str | None = None
    href: str | None = None
    is_visible: bool = True
    is_enabled: bool = True
    bounding_box: dict[str, float] | None = None  # {x, y, width, height}
    selector: str | None = None  # Internal use only — not for test authoring
    input_type: str | None = None

    @property
    def display_name(self) -> str:
        """Human-readable description of the element."""
        parts = [self.aria_label, self.text, self.placeholder, self.role, self.tag]
        return next((p for p in parts if p), "unknown element")


class ConsoleMessage(BaseModel):
    """A message captured from the browser console."""

    level: str  # "error", "warning", "info", "log"
    text: str
    url: str | None = None
    line: int | None = None
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def is_error(self) -> bool:
        return self.level in ("error", "assert")


class NetworkEvent(BaseModel):
    """A captured network request/response event."""

    url: str
    method: str = "GET"
    status: int | None = None
    resource_type: str | None = None
    error: str | None = None
    request_headers: dict[str, str] = Field(default_factory=dict)
    response_headers: dict[str, str] = Field(default_factory=dict)
    duration_ms: float | None = None
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def is_failure(self) -> bool:
        if self.error:
            return True
        if self.status and (self.status >= 400):
            return True
        return False

    @property
    def is_server_error(self) -> bool:
        return self.status is not None and self.status >= 500


class ActionRecord(BaseModel):
    """Records a single browser action — used for bug reproduction."""

    action_type: ActionType
    target_description: str
    selector: str | None = None
    value: str | None = None
    url_before: str
    url_after: str | None = None
    success: bool = True
    error_message: str | None = None
    screenshot_before: str | None = None
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_human_step(self) -> str:
        """Convert to a human-readable reproduction step."""
        action = self.action_type.value
        match action:
            case "navigate":
                return f"Navigate to {self.url_before}"
            case "click":
                return f'Click "{self.target_description}"'
            case "type":
                return f'Type "{self.value}" into "{self.target_description}"'
            case "scroll":
                return f"Scroll the page (direction: {self.value or 'down'})"
            case "hover":
                return f'Hover over "{self.target_description}"'
            case "press":
                return f"Press key: {self.value}"
            case "go_back":
                return "Navigate back"
            case "refresh":
                return "Refresh the page"
            case "resize":
                return f"Resize viewport to {self.value}"
            case "close_dialog":
                return "Close dialog/modal"
            case "select":
                return f'Select "{self.value}" from "{self.target_description}"'
            case _:
                return f"{action}: {self.target_description}"


class ViewportConfig(BaseModel):
    """A viewport size configuration."""

    name: str
    width: int
    height: int

    def __str__(self) -> str:
        return f"{self.width}x{self.height}"

    @property
    def label(self) -> str:
        return f"{self.name} ({self.width}x{self.height})"


# ─────────────────────────────────────────────────────────────────────────────
# Core models
# ─────────────────────────────────────────────────────────────────────────────


class PageState(BaseModel):
    """Complete snapshot of a page at a point in time."""

    url: str
    title: str = ""
    screenshot_path: str | None = None
    visible_elements: list[ElementInfo] = Field(default_factory=list)
    console_messages: list[ConsoleMessage] = Field(default_factory=list)
    network_events: list[NetworkEvent] = Field(default_factory=list)
    viewport: str = "1366x768"
    depth: int = 0
    parent_url: str | None = None
    actions_performed: list[ActionRecord] = Field(default_factory=list)
    page_hash: str = ""  # Content hash for deduplication
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    dom_metrics: dict[str, Any] = Field(default_factory=dict)
    accessibility_tree: list[dict] = Field(default_factory=list)

    @property
    def console_errors(self) -> list[ConsoleMessage]:
        return [m for m in self.console_messages if m.is_error]

    @property
    def network_failures(self) -> list[NetworkEvent]:
        return [e for e in self.network_events if e.is_failure]


class Finding(BaseModel):
    """
    A single detected QA issue.

    This is the primary output unit of the validation pipeline.
    Every validator produces a list of Finding objects.
    """

    id: str = Field(default_factory=lambda: f"BUG-{uuid.uuid4().hex[:6].upper()}")
    title: str
    severity: Severity
    category: Category
    url: str
    viewport: str = "unknown"
    description: str
    steps_to_reproduce: list[str] = Field(default_factory=list)
    expected: str = ""
    actual: str = ""
    confidence: float = 1.0  # 1.0 for deterministic; 0–1 for VLM findings
    reproduced: bool | None = None  # None = not attempted; True/False = result
    console_errors: list[str] = Field(default_factory=list)
    network_errors: list[str] = Field(default_factory=list)
    screenshot: str | None = None
    annotated_screenshot: str | None = None
    video_timestamp: float | None = None
    related_finding_ids: list[str] = Field(default_factory=list)
    raw_evidence: dict[str, Any] = Field(default_factory=dict)
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @field_validator("confidence")
    @classmethod
    def clamp_confidence(cls, v: float) -> float:
        return max(0.0, min(1.0, v))

    def to_dict(self) -> dict:
        return self.model_dump()


class ReproductionResult(BaseModel):
    """Result of attempting to reproduce a bug."""

    finding_id: str
    reproduced: bool
    steps_executed: list[str] = Field(default_factory=list)
    failure_reason: str | None = None
    confirmation_screenshot: str | None = None
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class QARunResult(BaseModel):
    """The complete result of a QA run — top-level output object."""

    run_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    target_url: str
    started_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    completed_at: str | None = None
    duration_seconds: float | None = None

    # Exploration stats
    pages_discovered: int = 0
    states_tested: int = 0
    actions_performed: int = 0
    viewports_tested: int = 0

    # Validation status
    validators_run: list[str] = Field(default_factory=list)
    validators_failed: list[str] = Field(default_factory=list)

    # Findings
    findings: list[Finding] = Field(default_factory=list)
    reproduction_results: list[ReproductionResult] = Field(default_factory=list)

    # Evidence paths
    screenshots_dir: str = ""
    recordings_dir: str = ""
    logs_dir: str = ""
    report_json_path: str = ""
    report_html_path: str = ""
    report_excel_path: str = ""
    report_docx_path: str = ""

    @property
    def perfection_score(self) -> float:
        """Calculate overall website perfection/health score (0-100%)."""
        penalties = {
            Severity.CRITICAL: 15.0,
            Severity.HIGH: 8.0,
            Severity.MEDIUM: 3.0,
            Severity.LOW: 1.0,
            Severity.INFO: 0.0,
        }
        total_penalty = sum(penalties.get(f.severity, 0.0) for f in self.findings)
        return max(0.0, round(100.0 - total_penalty, 1))

    @property
    def perfection_rating(self) -> str:
        """Human readable rating string for perfection score."""
        score = self.perfection_score
        if score >= 95:
            return "PERFECT"
        elif score >= 85:
            return "EXCELLENT"
        elif score >= 70:
            return "GOOD"
        elif score >= 50:
            return "NEEDS IMPROVEMENT"
        else:
            return "CRITICAL REFACTOR NEEDED"

    @property
    def critical_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == Severity.CRITICAL)

    @property
    def high_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == Severity.HIGH)

    @property
    def medium_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == Severity.MEDIUM)

    @property
    def low_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == Severity.LOW)

    @property
    def info_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == Severity.INFO)

    @property
    def reproduced_count(self) -> int:
        return sum(1 for r in self.reproduction_results if r.reproduced)

    @property
    def total_issues(self) -> int:
        return len(self.findings)
