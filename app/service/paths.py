"""XDG-style data/config/state paths for always-on installs (#88)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True, frozen=True)
class XdgAgentPaths:
    config_dir: Path
    data_dir: Path
    state_dir: Path
    config_path: Path
    env_path: Path
    log_path: Path
    runtime_dir: Path


def xdg_agent_paths(*, home: str | Path | None = None) -> XdgAgentPaths:
    """Return documented XDG-style paths for a non-checkout install."""

    base_home = Path(home or os.path.expanduser("~")).expanduser().resolve()
    config_home = Path(os.environ.get("XDG_CONFIG_HOME") or (base_home / ".config"))
    data_home = Path(
        os.environ.get("XDG_DATA_HOME") or (base_home / ".local" / "share")
    )
    state_home = Path(
        os.environ.get("XDG_STATE_HOME") or (base_home / ".local" / "state")
    )
    config_dir = config_home / "signal-agent"
    data_dir = data_home / "signal-agent"
    state_dir = state_home / "signal-agent"
    return XdgAgentPaths(
        config_dir=config_dir,
        data_dir=data_dir,
        state_dir=state_dir,
        config_path=config_dir / "config.yaml",
        env_path=config_dir / ".env",
        log_path=state_dir / "logs" / "signal-agent.log",
        runtime_dir=data_dir / "runtime",
    )
