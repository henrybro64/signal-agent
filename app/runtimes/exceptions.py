"""Project-owned runtime exceptions (#93)."""

from __future__ import annotations


class AgentRuntimeError(Exception):
    """Base class for agent runtime failures."""


class RuntimeTimeoutError(AgentRuntimeError):
    """Raised when a runtime call exceeds its timeout."""


class RuntimeProviderError(AgentRuntimeError):
    """Raised for configuration/transport failures."""


class RuntimeAuthError(RuntimeProviderError):
    """Raised when runtime credentials are missing/invalid."""


class RuntimePermissionError(RuntimeProviderError):
    """Raised when a requested action is denied by policy."""
