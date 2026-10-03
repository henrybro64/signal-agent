"""Optional browser readiness detection (#81)."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class BrowserDetection:
    ready: bool
    detail: str
    executable: str | None = None


_CANDIDATES = (
    "chromium",
    "chromium-browser",
    "google-chrome",
    "google-chrome-stable",
    "chrome",
)


def detect_browser() -> BrowserDetection:
    """Detect Chromium/Chrome without installing anything."""

    override = os.getenv("SIGNAL_AGENT_BROWSER_PATH")
    if override:
        if os.path.isfile(override) and os.access(override, os.X_OK):
            return BrowserDetection(
                ready=True,
                detail="configured via SIGNAL_AGENT_BROWSER_PATH",
                executable=override,
            )
        return BrowserDetection(
            ready=False,
            detail="SIGNAL_AGENT_BROWSER_PATH is set but not executable",
            executable=override,
        )

    for name in _CANDIDATES:
        path = shutil.which(name)
        if path:
            return BrowserDetection(
                ready=True,
                detail=f"found {name}",
                executable=path,
            )
    return BrowserDetection(
        ready=False,
        detail="no Chromium/Chrome executable found on PATH",
    )
