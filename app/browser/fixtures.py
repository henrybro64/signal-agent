"""Local HTML fixtures for offline browser tests (#87)."""

from __future__ import annotations

FIXTURE_URL = "https://fixture.local/signal-agent-test"


def default_fixtures() -> dict[str, tuple[str, str]]:
    """Return url → (title, text) fixtures used by FakeBrowserBackend."""

    return {
        FIXTURE_URL: (
            "signal-agent browser test",
            "Signal-agent browser fixture page. No network required.",
        ),
        "https://example.com/": (
            "Example Domain",
            "This domain is for use in documentation examples.",
        ),
    }
