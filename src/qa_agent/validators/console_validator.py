"""
ConsoleValidator — detects JavaScript console errors and uncaught exceptions.

Uses Playwright's console event listener (set up in PageController).
This is a deterministic validator — no AI involved.

Severity rules:
- Uncaught exception / page error → HIGH
- console.error → HIGH
- Multiple console.errors on same page → escalate to CRITICAL if > 5
- console.warning → LOW
"""

from __future__ import annotations

from qa_agent.utils.schemas import Category, Finding, PageState, Severity
from qa_agent.validators.base import BaseValidator

# Patterns that indicate noise rather than real bugs
_NOISE_PATTERNS = [
    "extension",
    "chrome-extension://",
    "favicon",
    "gtm",
    "google-analytics",
    "analytics.js",
    "[Violation]",
    "non-passive event listener",
    "third-party cookie",
    "deprecated",
]


def _is_noise(message: str) -> bool:
    msg_lower = message.lower()
    return any(pattern.lower() in msg_lower for pattern in _NOISE_PATTERNS)


class ConsoleValidator(BaseValidator):
    """
    Validates JavaScript console output for errors and exceptions.

    Findings produced:
    - JS_CONSOLE_ERROR: Individual console.error entries
    - JS_UNCAUGHT_EXCEPTION: Uncaught JS exceptions
    """

    async def validate(self, page_state: PageState, **kwargs) -> list[Finding]:
        findings = []
        error_messages = page_state.console_errors

        if not error_messages:
            return []

        # Filter out known third-party noise
        real_errors = [m for m in error_messages if not _is_noise(m.text)]
        if not real_errors:
            return []

        # Deduplicate by message text (same error firing multiple times)
        seen: set[str] = set()
        unique_errors = []
        for msg in real_errors:
            key = msg.text[:200]
            if key not in seen:
                seen.add(key)
                unique_errors.append(msg)

        # Escalate severity for many errors
        severity = Severity.HIGH
        if len(unique_errors) > 5:
            severity = Severity.CRITICAL

        for msg in unique_errors:
            is_exception = "Uncaught" in msg.text or "uncaught" in msg.text

            finding = Finding(
                title=f"Console error: {msg.text[:80]}",
                severity=Severity.CRITICAL if is_exception else severity,
                category=Category.CONSOLE,
                url=page_state.url,
                viewport=page_state.viewport,
                description=(
                    f"A JavaScript {'uncaught exception' if is_exception else 'console error'} "
                    f"was detected on this page.\n\n"
                    f"Message: {msg.text}\n"
                    f"URL: {msg.url or page_state.url}"
                ),
                steps_to_reproduce=[
                    f"Navigate to {page_state.url}",
                    "Open browser developer tools",
                    "Inspect the Console tab",
                    f"Observe the error: {msg.text[:100]}",
                ],
                expected="No JavaScript errors in console",
                actual=f"Console error: {msg.text[:200]}",
                confidence=1.0,
                screenshot=page_state.screenshot_path,
                raw_evidence={"console_message": msg.model_dump()},
            )
            findings.append(finding)

        self.logger.info(f"ConsoleValidator: {len(findings)} finding(s) on {page_state.url}")
        return findings
