"""
NetworkValidator — detects HTTP failures, failed requests, and server errors.

This is a deterministic validator using Playwright's network event listeners.

Severity rules:
- HTTP 5xx → HIGH (server error)
- HTTP 5xx on main document → CRITICAL
- HTTP 4xx on navigation resource → HIGH
- HTTP 4xx on sub-resource (image, script) → MEDIUM
- Request failed (network error) → MEDIUM
- Failed API request → HIGH
"""

from __future__ import annotations

from urllib.parse import urlparse

from qa_agent.utils.schemas import Category, Finding, NetworkEvent, PageState, Severity
from qa_agent.validators.base import BaseValidator

# Resource types that are considered "primary" (failures are more impactful)
_PRIMARY_RESOURCE_TYPES = {"document", "xhr", "fetch", "websocket"}
_SECONDARY_RESOURCE_TYPES = {"script", "stylesheet", "font"}

# URL patterns to ignore (third-party analytics, etc.)
_IGNORE_URL_PATTERNS = [
    "google-analytics.com",
    "googletagmanager.com",
    "doubleclick.net",
    "facebook.com/tr",
    "connect.facebook.net",
    "hotjar.com",
    "analytics",
    "segment.io",
    "mixpanel.com",
]


def _should_ignore(url: str) -> bool:
    url_lower = url.lower()
    return any(pattern in url_lower for pattern in _IGNORE_URL_PATTERNS)


def _get_failure_severity(event: NetworkEvent, page_domain: str) -> Severity:
    if event.is_server_error:
        if event.resource_type == "document":
            return Severity.CRITICAL
        if event.resource_type in _PRIMARY_RESOURCE_TYPES:
            return Severity.HIGH
        return Severity.MEDIUM

    if event.status and event.status >= 400:
        if event.resource_type in _PRIMARY_RESOURCE_TYPES:
            return Severity.HIGH
        return Severity.MEDIUM

    if event.error:
        if event.resource_type in _PRIMARY_RESOURCE_TYPES:
            return Severity.HIGH
        return Severity.MEDIUM

    return Severity.LOW


class NetworkValidator(BaseValidator):
    """
    Validates network activity for HTTP failures and request errors.
    """

    async def validate(self, page_state: PageState, **kwargs) -> list[Finding]:
        failures = page_state.network_failures
        if not failures:
            return []

        findings = []
        seen_urls: set[str] = set()

        for event in failures:
            if _should_ignore(event.url):
                continue
            if event.url in seen_urls:
                continue
            seen_urls.add(event.url)

            try:
                page_domain = urlparse(page_state.url).netloc
                event_domain = urlparse(event.url).netloc
                is_same_domain = event_domain == page_domain or event_domain.endswith("." + page_domain)
            except Exception:
                is_same_domain = False

            severity = _get_failure_severity(event, page_domain if is_same_domain else "")

            if event.error:
                title = f"Request failed: {event.url[:60]}"
                description = (
                    f"Network request failed with error: {event.error}\n"
                    f"URL: {event.url}\n"
                    f"Method: {event.method}\n"
                    f"Resource type: {event.resource_type or 'unknown'}"
                )
                actual = f"Request failed: {event.error}"
            else:
                title = f"HTTP {event.status}: {event.url[:60]}"
                description = (
                    f"HTTP {event.status} response received for a {event.resource_type or 'unknown'} request.\n"
                    f"URL: {event.url}\n"
                    f"Method: {event.method}"
                )
                actual = f"HTTP {event.status} response"

            finding = Finding(
                title=title,
                severity=severity,
                category=Category.NETWORK,
                url=page_state.url,
                viewport=page_state.viewport,
                description=description,
                steps_to_reproduce=[
                    f"Navigate to {page_state.url}",
                    "Open browser developer tools > Network tab",
                    f"Observe failed request to: {event.url}",
                ],
                expected="All network requests should return successful responses (2xx/3xx)",
                actual=actual,
                confidence=1.0,
                screenshot=page_state.screenshot_path,
                network_errors=[f"{event.method} {event.url} → {event.status or event.error}"],
                raw_evidence={"network_event": event.model_dump()},
            )
            findings.append(finding)

        self.logger.info(f"NetworkValidator: {len(findings)} finding(s) on {page_state.url}")
        return findings
