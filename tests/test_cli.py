import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(
    shutil.which("signal-agent") is None,
    reason="signal-agent console script is not installed in this environment",
)
def test_signal_agent_console_script_lists_tasks() -> None:
    result = subprocess.run(
        ["signal-agent", "--list-tasks"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout.splitlines() == ["dummy", "fetch"]
