"""
Unit tests for report generation.
Tests that reports are correctly generated without a real browser run.
"""

import json
import pytest
from pathlib import Path
from qa_agent.utils.schemas import Finding, QARunResult, Severity, Category
from qa_agent.reporting.generator import ReportGenerator


def make_result(**kwargs) -> QARunResult:
    defaults = {
        "target_url": "https://example.com",
        "run_id": "test123",
        "validators_run": ["console", "network", "images"],
        "pages_discovered": 5,
        "states_tested": 10,
        "actions_performed": 25,
        "viewports_tested": 2,
        "duration_seconds": 42.5,
    }
    defaults.update(kwargs)
    return QARunResult(**defaults)


def make_finding(severity=Severity.HIGH, **kwargs) -> Finding:
    defaults = {
        "title": "Test finding",
        "severity": severity,
        "category": Category.CONSOLE,
        "url": "https://example.com",
        "description": "A test finding description",
        "steps_to_reproduce": ["Step 1", "Step 2"],
        "expected": "No errors",
        "actual": "Error occurred",
    }
    defaults.update(kwargs)
    return Finding(**defaults)


@pytest.fixture
def reports_dir(tmp_path):
    return tmp_path / "reports"


class TestReportGenerator:
    @pytest.mark.asyncio
    async def test_generate_json_creates_file(self, reports_dir):
        gen = ReportGenerator(reports_dir=reports_dir)
        result = make_result()
        path = await gen.generate_json(result)
        assert Path(path).exists()

    @pytest.mark.asyncio
    async def test_generate_json_valid_json(self, reports_dir):
        gen = ReportGenerator(reports_dir=reports_dir)
        result = make_result()
        path = await gen.generate_json(result)
        with open(path) as f:
            data = json.load(f)
        assert data["target_url"] == "https://example.com"

    @pytest.mark.asyncio
    async def test_generate_json_includes_findings(self, reports_dir):
        gen = ReportGenerator(reports_dir=reports_dir)
        result = make_result()
        result.findings = [make_finding(), make_finding(severity=Severity.LOW)]
        path = await gen.generate_json(result)
        with open(path) as f:
            data = json.load(f)
        assert len(data["findings"]) == 2

    @pytest.mark.asyncio
    async def test_generate_json_sorted_by_severity(self, reports_dir):
        gen = ReportGenerator(reports_dir=reports_dir)
        result = make_result()
        result.findings = [
            make_finding(severity=Severity.LOW, title="low"),
            make_finding(severity=Severity.CRITICAL, title="critical"),
            make_finding(severity=Severity.MEDIUM, title="medium"),
        ]
        path = await gen.generate_json(result)
        with open(path) as f:
            data = json.load(f)
        severities = [f["severity"] for f in data["findings"]]
        # CRITICAL should be first
        assert severities[0] == "CRITICAL"
        assert severities[-1] == "LOW"

    @pytest.mark.asyncio
    async def test_generate_html_creates_file(self, reports_dir):
        gen = ReportGenerator(reports_dir=reports_dir)
        result = make_result()
        path = await gen.generate_html(result)
        assert Path(path).exists()

    @pytest.mark.asyncio
    async def test_generate_html_contains_target_url(self, reports_dir):
        gen = ReportGenerator(reports_dir=reports_dir)
        result = make_result()
        path = await gen.generate_html(result)
        content = Path(path).read_text(encoding="utf-8")
        assert "https://example.com" in content

    @pytest.mark.asyncio
    async def test_generate_html_contains_finding_title(self, reports_dir):
        gen = ReportGenerator(reports_dir=reports_dir)
        result = make_result()
        result.findings = [make_finding(title="My specific bug title")]
        path = await gen.generate_html(result)
        content = Path(path).read_text(encoding="utf-8")
        assert "My specific bug title" in content

    @pytest.mark.asyncio
    async def test_empty_findings_generates_valid_html(self, reports_dir):
        gen = ReportGenerator(reports_dir=reports_dir)
        result = make_result()
        result.findings = []
        path = await gen.generate_html(result)
        content = Path(path).read_text(encoding="utf-8")
        assert "<!DOCTYPE html>" in content
        assert "No issues detected" in content
