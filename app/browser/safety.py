"""Bounded action policy for interactive browser sessions (#106)."""

from __future__ import annotations

from app.browser.models import InteractiveElement, SessionSnapshot
from app.browser.policy import BrowserPolicyError

_ALLOWED_ROLES = frozenset(
    {"link", "button", "textbox", "searchbox", "combobox", "checkbox", "select"}
)
_TYPABLE_ROLES = frozenset({"textbox", "searchbox", "combobox"})
_SELECTABLE_ROLES = frozenset({"combobox", "select"})
_SCROLL_DIRECTIONS = frozenset({"up", "down", "top", "bottom"})
_FILE_INPUT_TYPES = frozenset({"file"})
_TYPABLE_INPUT_TYPES = frozenset(
    {
        "text",
        "search",
        "email",
        "tel",
        "url",
        "number",
        "",
        None,
    }
)
_SEARCH_LABEL_TOKENS = ("search", "filter", "query", "keyword", "find")

# Phrase-level markers only. Avoid the bare substring "captcha", which matches
# words like "recapture".
_CHALLENGE_MARKERS = (
    "verify you are human",
    "are you a robot",
    "i'm not a robot",
    "i am not a robot",
    "recaptcha",
    "hcaptcha",
    "h-captcha",
    "cf-turnstile",
    "cloudflare challenge",
    "checking your browser before accessing",
    "complete the security check",
    "please complete the captcha",
    "solve the captcha",
    "captcha challenge",
    "attention required! | cloudflare",
)

_BLOCKED_LABEL_HINTS = (
    "log in",
    "login",
    "sign in",
    "signin",
    "apply",
    "captcha",
    "password",
    "credit card",
    "ssn",
    "message recruiter",
    "send message",
    "contact recruiter",
    "message hiring",
    "inmail",
)


def assert_element_allowed(element: InteractiveElement) -> None:
    """Reject file inputs, login/apply controls, and unknown roles."""

    input_type = (element.input_type or "").strip().lower()
    if input_type in _FILE_INPUT_TYPES:
        raise BrowserPolicyError("File uploads are not allowed")
    role = (element.role or "").strip().lower()
    if role not in _ALLOWED_ROLES:
        raise BrowserPolicyError(
            f"Refusing to act on role={role!r}; allowlisted roles are "
            + ", ".join(sorted(_ALLOWED_ROLES))
        )
    haystack = " ".join(
        part
        for part in (element.label, element.role, element.input_type, element.url)
        if part
    ).casefold()
    for hint in _BLOCKED_LABEL_HINTS:
        if hint in haystack:
            raise BrowserPolicyError(
                f"Refusing to interact with a blocked control ({hint!r})"
            )


def assert_type_allowed(element: InteractiveElement, text: str) -> None:
    """Allow typing only into search/filter text fields (no passwords/files)."""

    _ = text
    assert_element_allowed(element)
    role = (element.role or "").strip().lower()
    if role not in _TYPABLE_ROLES:
        raise BrowserPolicyError(
            "Typing is limited to search/filter text fields "
            f"(role={role!r} is not textbox/searchbox)"
        )
    input_type = (element.input_type or "").strip().lower() or None
    if input_type == "password":
        raise BrowserPolicyError("Typing into password fields is not allowed")
    if input_type not in _TYPABLE_INPUT_TYPES:
        raise BrowserPolicyError(
            f"Typing is not allowed into input type={input_type!r}"
        )
    if not _is_search_or_filter_field(element):
        raise BrowserPolicyError(
            "Typing is limited to search/filter fields "
            f"(label={element.label!r} is not a search/filter control)"
        )


def assert_element_action_allowed(
    element: InteractiveElement,
    *,
    action: str,
) -> None:
    """Dispatch click/type/select policy checks used by the session manager."""

    if action == "type":
        assert_type_allowed(element, "")
        return
    if action == "select":
        assert_select_allowed(element)
        return
    assert_element_allowed(element)


def apply_challenge_status(snap: SessionSnapshot) -> SessionSnapshot:
    """Mark snapshots that look like CAPTCHA / bot-check walls."""

    if snap.status == "blocked_by_challenge" and snap.blocked_reason:
        return snap
    reason = detect_challenge(title=snap.title, text=snap.text)
    if reason is None:
        return snap
    return SessionSnapshot(
        session_id=snap.session_id,
        url=snap.url,
        title=snap.title,
        text=snap.text,
        elements=[],
        status="blocked_by_challenge",
        screenshot_path=snap.screenshot_path,
        blocked_reason=reason,
        steps_used=snap.steps_used,
    )


def _is_search_or_filter_field(element: InteractiveElement) -> bool:
    role = (element.role or "").strip().lower()
    input_type = (element.input_type or "").strip().lower()
    label = (element.label or "").casefold()
    if role == "searchbox" or input_type == "search":
        return True
    if role in {"textbox", "combobox"} and any(
        token in label for token in _SEARCH_LABEL_TOKENS
    ):
        return True
    return False


def assert_select_allowed(element: InteractiveElement) -> None:
    assert_element_allowed(element)
    role = (element.role or "").strip().lower()
    if role not in _SELECTABLE_ROLES:
        raise BrowserPolicyError(
            f"select is only allowed on combobox/select controls (got {role!r})"
        )


def assert_scroll_direction(direction: str) -> str:
    value = direction.strip().lower()
    if value not in _SCROLL_DIRECTIONS:
        raise BrowserPolicyError(
            f"Unsupported scroll direction {direction!r}; use up, down, top, or bottom"
        )
    return value


def detect_challenge(*, title: str, text: str, html: str = "") -> str | None:
    """Return a reason if the page looks like a CAPTCHA / bot-check wall."""

    haystack = " ".join((title, text, html)).casefold()
    for marker in _CHALLENGE_MARKERS:
        if marker in haystack:
            return f"Possible anti-bot challenge detected ({marker!r})"
    return None
