from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_docker_compose_signal_agent_service_matches_issue_21_contract() -> None:
    compose = yaml.safe_load(
        (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    )

    assert isinstance(compose, dict)
    assert compose["services"]["signal-agent"] == {
        "build": {
            "context": ".",
            "dockerfile": "Dockerfile",
        },
        "image": "signal-agent:local",
        "working_dir": "/app",
        "env_file": [".env"],
        "volumes": [
            "./config.yaml:/app/config.yaml:ro",
            "./data:/app/data",
        ],
        "command": ["python", "main.py", "--help"],
    }
