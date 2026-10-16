"""Local HTML fixtures for offline browser tests (#87/#106)."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.browser.models import InteractiveElement

FIXTURE_URL = "https://fixture.local/signal-agent-test"
JOBS_PAGE_1 = "https://fixture.local/jobs"
JOBS_PAGE_2 = "https://fixture.local/jobs?page=2"
JOBS_PAGE_3 = "https://fixture.local/jobs?page=3"
CHALLENGE_URL = "https://fixture.local/challenge"
OFF_DOMAIN_URL = "https://evil.example/redirect-target"
SEARCH_RESULTS_URL = "https://fixture.local/jobs?q=python"


@dataclass(slots=True)
class FixturePage:
    title: str
    text: str
    elements: list[InteractiveElement] = field(default_factory=list)
    html: str | None = None


def default_fixtures() -> dict[str, tuple[str, str]]:
    """Return url → (title, text) fixtures used by one-shot FakeBrowserBackend."""

    pages = interactive_fixture_pages()
    return {url: (page.title, page.text) for url, page in pages.items()}


def interactive_fixture_pages() -> dict[str, FixturePage]:
    """Multi-page graph for session-based FakeSessionBackend tests (#106)."""

    return {
        FIXTURE_URL: FixturePage(
            title="signal-agent browser test",
            text="Signal-agent browser fixture page. No network required.",
        ),
        "https://example.com/": FixturePage(
            title="Example Domain",
            text="This domain is for use in documentation examples.",
            elements=[
                InteractiveElement(
                    ref=1,
                    role="link",
                    label="More information",
                    url="https://example.com/more",
                ),
            ],
        ),
        JOBS_PAGE_1: FixturePage(
            title="Jobs page 1",
            text="Job results page 1: Backend Engineer at Acme.",
            elements=[
                InteractiveElement(
                    ref=1,
                    role="link",
                    label="Next page",
                    url=JOBS_PAGE_2,
                ),
                InteractiveElement(
                    ref=2,
                    role="button",
                    label="Show more",
                ),
                InteractiveElement(
                    ref=3,
                    role="textbox",
                    label="Search jobs",
                    input_type="text",
                ),
                InteractiveElement(
                    ref=4,
                    role="textbox",
                    label="Password",
                    input_type="password",
                ),
                InteractiveElement(
                    ref=5,
                    role="button",
                    label="Apply now",
                ),
                InteractiveElement(
                    ref=6,
                    role="link",
                    label="External partner",
                    url=OFF_DOMAIN_URL,
                ),
                InteractiveElement(
                    ref=7,
                    role="combobox",
                    label="Location filter",
                ),
                InteractiveElement(
                    ref=8,
                    role="textbox",
                    label="Cover letter notes",
                    input_type="text",
                ),
                InteractiveElement(
                    ref=9,
                    role="generic",
                    label="Resume upload",
                    input_type="file",
                ),
                InteractiveElement(
                    ref=10,
                    role="button",
                    label="Message recruiter",
                ),
            ],
        ),
        JOBS_PAGE_2: FixturePage(
            title="Jobs page 2",
            text="Job results page 2: Platform Engineer at Globex.",
            elements=[
                InteractiveElement(
                    ref=1,
                    role="link",
                    label="Next page",
                    url=JOBS_PAGE_3,
                ),
                InteractiveElement(
                    ref=2,
                    role="link",
                    label="Previous page",
                    url=JOBS_PAGE_1,
                ),
            ],
        ),
        JOBS_PAGE_3: FixturePage(
            title="Jobs page 3",
            text="Job results page 3: Staff Agent Engineer at Initech.",
            elements=[
                InteractiveElement(
                    ref=1,
                    role="link",
                    label="Previous page",
                    url=JOBS_PAGE_2,
                ),
            ],
        ),
        SEARCH_RESULTS_URL: FixturePage(
            title="Jobs search python",
            text="Filtered job results for python.",
            elements=[
                InteractiveElement(
                    ref=1,
                    role="textbox",
                    label="Search jobs",
                    input_type="text",
                ),
            ],
        ),
        CHALLENGE_URL: FixturePage(
            title="Just a moment...",
            text="Checking your browser before accessing fixture.local. cf-challenge",
            html="<div id='cf-challenge'>Enable JavaScript and cookies</div>",
            elements=[],
        ),
        OFF_DOMAIN_URL: FixturePage(
            title="Off domain",
            text="This host is not on the allowlist.",
        ),
    }
