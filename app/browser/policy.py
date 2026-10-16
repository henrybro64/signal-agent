"""Domain/timeout/size policy for browser tools (#87)."""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlparse


class BrowserPolicyError(ValueError):
    """Raised when a navigation request violates browser policy."""


@dataclass(slots=True)
class BrowserPolicy:
    """Allow/deny domains and resource bounds."""

    allowed_domains: list[str] = field(default_factory=list)
    denied_domains: list[str] = field(default_factory=list)
    navigation_timeout_seconds: float = 15.0
    max_page_bytes: int = 500_000
    allow_downloads: bool = False
    allow_javascript: bool = False
    allow_subresources: bool = False

    def assert_url_not_denied(self, url: str) -> None:
        """Reject invalid schemes, missing hosts, and denied_domains."""

        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            raise BrowserPolicyError(
                f"URL scheme {parsed.scheme!r} is not allowed (http/https only)"
            )
        host = (parsed.hostname or "").lower().rstrip(".")
        if not host:
            raise BrowserPolicyError("URL is missing a hostname")
        for denied in self.denied_domains:
            if _host_matches(host, denied.lower()):
                raise BrowserPolicyError(f"Domain {host!r} is denied by policy")

    def assert_url_allowlisted(self, url: str) -> None:
        """Reject hosts that are not in allowed_domains."""

        host = (urlparse(url).hostname or "").lower().rstrip(".")
        if not self.allowed_domains:
            raise BrowserPolicyError(
                "No allowed_domains configured; refusing navigation"
            )
        allowed_hit = any(
            _host_matches(host, allowed.lower()) for allowed in self.allowed_domains
        )
        if not allowed_hit:
            raise BrowserPolicyError(
                f"Domain {host!r} is not in browser.allowed_domains"
            )

    def assert_url_allowed(self, url: str) -> None:
        self.assert_url_not_denied(url)
        self.assert_url_allowlisted(url)


def _host_matches(host: str, pattern: str) -> bool:
    pattern = pattern.strip().lower().lstrip(".")
    if not pattern:
        return False
    return host == pattern or host.endswith("." + pattern)
