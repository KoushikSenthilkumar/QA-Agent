"""
LinkValidator — detects broken links (404, 5xx, unreachable).

Uses aiohttp to check link status codes without full browser navigation.
Only checks same-domain links by default to avoid hammering external sites.

Severity rules:
- 5xx response on same-domain link → HIGH
- 4xx response on same-domain navigation link → HIGH
- 4xx response on same-domain non-navigation link → MEDIUM
- External link returning 4xx/5xx → LOW (we control less)
- Request timeout/error → MEDIUM
"""

from __future__ import annotations

import asyncio
from urllib.parse import urljoin, urlparse

import aiohttp

from qa_agent.utils.schemas import Category, Finding, PageState, Severity
from qa_agent.validators.base import BaseValidator

# Maximum links to check per page (avoid excessive HTTP requests)
_MAX_LINKS_PER_PAGE = 30
_REQUEST_TIMEOUT = 10
_CONCURRENCY = 5


class LinkValidator(BaseValidator):
    """
    Validates hyperlinks by making HEAD requests to check their status.
    """

    def __init__(self, check_external: bool = False):
        super().__init__()
        self.check_external = check_external

    async def validate(self, page_state: PageState, **kwargs) -> list[Finding]:
        links: list[dict] = kwargs.get("links", [])
        if not links:
            return []

        try:
            page_domain = urlparse(page_state.url).netloc
        except Exception:
            return []

        # Filter and normalize links
        links_to_check = []
        seen_hrefs: set[str] = set()
        for link in links:
            href = link.get("href", "")
            if not href:
                continue
            # Resolve relative URLs
            if not href.startswith("http"):
                href = urljoin(page_state.url, href)
            # Skip duplicates, fragments, and special schemes
            parsed = urlparse(href)
            clean_href = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
            if clean_href in seen_hrefs:
                continue
            seen_hrefs.add(clean_href)

            is_external = parsed.netloc != page_domain
            if is_external and not self.check_external:
                continue

            links_to_check.append({
                "href": clean_href,
                "text": link.get("text", ""),
                "is_external": is_external,
            })

        links_to_check = links_to_check[:_MAX_LINKS_PER_PAGE]
        if not links_to_check:
            return []

        # Check links concurrently with a semaphore
        results = await self._check_links_batch(links_to_check)

        findings = []
        for link_info, status, error in results:
            if status is None and error is None:
                continue  # Success or skip
            if status and 200 <= status < 400:
                continue  # Redirect is okay

            href = link_info["href"]
            text = link_info.get("text", "")
            is_external = link_info.get("is_external", False)

            if error:
                severity = Severity.MEDIUM
                actual = f"Request failed: {error}"
                title = f"Link unreachable: {href[:60]}"
            elif status and status >= 500:
                severity = Severity.HIGH if not is_external else Severity.LOW
                actual = f"HTTP {status} response"
                title = f"HTTP {status} on link: {href[:60]}"
            elif status and status >= 400:
                severity = Severity.MEDIUM if not is_external else Severity.LOW
                actual = f"HTTP {status} response"
                title = f"HTTP {status} (broken link): {href[:60]}"
            else:
                continue

            finding = Finding(
                title=title,
                severity=severity,
                category=Category.LINK,
                url=page_state.url,
                viewport=page_state.viewport,
                description=(
                    f"A hyperlink on this page leads to a broken or unavailable destination.\n\n"
                    f"Link text: {text or '(no text)'}\n"
                    f"Destination: {href}\n"
                    f"Status: {actual}\n"
                    f"External: {is_external}"
                ),
                steps_to_reproduce=[
                    f"Navigate to {page_state.url}",
                    f"Click the link with text: '{text or href[:40]}'",
                    f"Observe: {actual}",
                ],
                expected="Link should lead to a valid, accessible page (2xx/3xx)",
                actual=actual,
                confidence=1.0,
                screenshot=page_state.screenshot_path,
            )
            findings.append(finding)

        self.logger.info(
            f"LinkValidator: checked {len(links_to_check)} links, "
            f"{len(findings)} finding(s) on {page_state.url}"
        )
        return findings

    async def _check_links_batch(
        self,
        links: list[dict],
    ) -> list[tuple[dict, int | None, str | None]]:
        """Check links concurrently. Returns list of (link, status, error)."""
        semaphore = asyncio.Semaphore(_CONCURRENCY)
        timeout = aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT)

        async with aiohttp.ClientSession(timeout=timeout) as session:
            tasks = [self._check_single_link(session, semaphore, link) for link in links]
            return await asyncio.gather(*tasks, return_exceptions=False)

    async def _check_single_link(
        self,
        session: aiohttp.ClientSession,
        semaphore: asyncio.Semaphore,
        link: dict,
    ) -> tuple[dict, int | None, str | None]:
        href = link["href"]
        async with semaphore:
            try:
                async with session.head(
                    href,
                    allow_redirects=True,
                    headers={"User-Agent": "QA-Agent/0.1 (link-checker)"},
                ) as resp:
                    return (link, resp.status, None)
            except asyncio.TimeoutError:
                return (link, None, "timeout")
            except aiohttp.ClientConnectorError as e:
                return (link, None, f"connection error: {e.strerror or 'refused'}")
            except Exception as e:
                return (link, None, str(e)[:80])
