"""Configured bounded runtime entrypoint."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from app.core.config import load_config
from app.core.secrets import (
    llm_api_keys_from_env,
    load_env_settings,
    resolve_provider_credentials,
    secret_values,
)
from app.llm.models_cli import build_routed_provider
from app.runtime import paths
from app.tools.permissions import policy_from_app_config
from app.tools.redaction import redact_value

from .exceptions import AgentRuntimeError
from .factory import create_agent_runtime
from .models import RuntimeRequest


def run_agent_command(args: Any) -> int:
    config = load_config(args.config)
    env = resolve_provider_credentials(
        load_env_settings(paths.env_path(args.root)), config.providers
    )
    secrets = secret_values(env)
    policy = policy_from_app_config(
        config,
        root=Path(args.root),
        interactive=sys.stdin.isatty(),
        secrets=secrets,
        approve=lambda _name, _args, summary: (
            input(f"{summary}\nApprove? [y/N] ").lower() in {"y", "yes"}
        ),
    )
    provider = build_routed_provider(config.llm, api_keys=llm_api_keys_from_env(env))
    try:
        runtime = create_agent_runtime(
            runtime=config.runtime.default,
            api_key=env.codex_api_key,
            provider=provider,
            model=config.runtime.model,
            policy=policy,
        )
        result = runtime.execute(
            RuntimeRequest(
                goal=args.goal,
                allowed_actions=(args.action,),
                timeout_seconds=args.timeout,
            )
        )
    except AgentRuntimeError as exc:
        print(redact_value(str(exc), secrets), file=sys.stderr)
        return 1
    print(redact_value(result.summary, secrets))
    return 0 if result.ok else 1
