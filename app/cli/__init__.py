"""Command-line entrypoint for signal-agent (#81/#82/#83)."""

from __future__ import annotations

import sys
import time
from typing import Any

from app.cli.legacy import (
    DEFAULT_CONFIG_PATH,
    print_health_report,
    print_recent_runs,
    run_eval_command,
    run_langchain_smoke_command,
    run_mcp_serve_command,
    run_task_with_failure_alerts,
    send_telegram_test_message,
)
from app.cli.parser import build_parser, resolve_runtime_args
from app.tasks import build_task, list_tasks


def main(argv: list[str] | None = None) -> int:
    """Dispatch subcommands or legacy flags; return a process exit code."""

    parser = build_parser()
    # Global options (`--config`, `--root`) may appear before or after the
    # subcommand; resolve_runtime_args normalizes defaults under --root.
    args = resolve_runtime_args(parser.parse_args(argv))

    try:
        if args.command:
            return _dispatch_command(args)

        # Legacy flag path for backward compatibility.
        if args.list_tasks:
            print("\n".join(list_tasks()))
            return 0
        if args.health:
            print_health_report()
            return 0
        if args.recent_runs:
            print_recent_runs(limit=args.limit)
            return 0
        if args.telegram_test is not None:
            send_telegram_test_message(args.telegram_test)
            return 0
        if args.langchain_smoke:
            return run_langchain_smoke_command()
        if args.eval is not None:
            return run_eval_command(suite=args.eval)
        if args.mcp_serve:
            return run_mcp_serve_command(
                host=args.mcp_host,
                port=args.mcp_port,
                transport=args.mcp_transport,
                config_path=args.config,
            )
        if args.task is not None:
            run_task_with_failure_alerts(args.task, config_path=args.config)
            return 0

        parser.print_help()
        return 0
    except KeyError as exc:
        print(f"Unknown schedule id: {exc.args[0]}", file=sys.stderr)
        return 1
    except (FileNotFoundError, ValueError) as exc:
        # ConfigError/HealthError/SecretError/TelegramNotifierError subclass
        # ValueError; listing them alongside ValueError triggers overlapping-
        # exception lint findings.
        print(exc, file=sys.stderr)
        return 1


def _dispatch_command(args: Any) -> int:
    handlers = {
        "setup": _cmd_setup,
        "config": _cmd_config,
        "doctor": _cmd_doctor,
        "start": _cmd_start,
        "stop": _cmd_stop,
        "restart": _cmd_restart,
        "status": _cmd_status,
        "chat": _cmd_chat,
        "run": _cmd_run,
        "tasks": _cmd_tasks,
        "models": _cmd_models,
        "tools": _cmd_tools,
        "schedules": _cmd_schedules,
        "schedule": _cmd_schedule,
        "logs": _cmd_logs,
    }
    command = getattr(args, "command", None)
    handler = handlers.get(command) if isinstance(command, str) else None
    if handler is None:
        print(f"Unknown command: {command}", file=sys.stderr)
        return 2
    return handler(args)


def _cmd_tasks(_args: Any) -> int:
    print("\n".join(list_tasks()))
    return 0


def _cmd_setup(args: Any) -> int:
    from app.setup import (
        SetupOptions,
        format_validation_report,
        redact_setup_text,
        run_setup,
    )

    options = SetupOptions(
        provider=args.provider,
        model=args.model,
        llm_enabled=bool(args.llm_enabled),
        openai_api_key=args.openai_api_key,
        telegram_enabled=bool(args.telegram_enabled),
        telegram_bot_token=args.telegram_bot_token,
        telegram_chat_id=args.telegram_chat_id,
        non_interactive=bool(args.non_interactive),
        force=bool(args.force),
    )
    result = run_setup(root=args.root, options=options)
    print(redact_setup_text(f"config: {result.config_path}"))
    print(redact_setup_text(f"env: {result.env_path}"))
    print(redact_setup_text(f"database: {result.db_path}"))
    print(redact_setup_text(f"browser: {result.browser_detail}"))
    print(redact_setup_text(format_validation_report(result.validation)))
    return 0 if result.validation.ok else 1


def _cmd_config(args: Any) -> int:
    from app.setup import redact_setup_text, show_config_redacted

    if args.config_command != "show":
        print("Usage: signal-agent config show", file=sys.stderr)
        return 2
    print(
        redact_setup_text(show_config_redacted(root=args.root, config_path=args.config))
    )
    return 0


def _cmd_doctor(args: Any) -> int:
    from app.runtime import collect_status, format_status_report
    from app.setup import (
        format_validation_report,
        redact_setup_text,
        validate_local_setup,
    )

    report = validate_local_setup(root=args.root, config_path=args.config)
    status = collect_status(root=args.root, config_path=args.config)
    print(redact_setup_text(format_validation_report(report)))
    print()
    print(redact_setup_text(format_status_report(status)))
    return 0 if report.ok else 1


def _cmd_start(args: Any) -> int:
    from app.runtime import RuntimeLifecycleError, start_runtime

    try:
        info = start_runtime(root=args.root, config_path=args.config, detach=True)
    except RuntimeLifecycleError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(f"Runtime started pid={info.pid}")
    return 0


def _cmd_stop(args: Any) -> int:
    from app.runtime import stop_runtime

    info = stop_runtime(root=args.root)
    print(f"Runtime {info.detail}")
    return 0


def _cmd_restart(args: Any) -> int:
    from app.runtime import RuntimeLifecycleError, restart_runtime

    try:
        info = restart_runtime(root=args.root, config_path=args.config)
    except RuntimeLifecycleError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(f"Runtime restarted pid={info.pid}")
    return 0


def _cmd_status(args: Any) -> int:
    from app.runtime import collect_status, format_status_report

    print(format_status_report(collect_status(root=args.root, config_path=args.config)))
    return 0


def _cmd_chat(args: Any) -> int:
    from app.chat import ChatBudgets, run_chat_repl

    token_budget = args.max_estimated_tokens or None
    return run_chat_repl(
        root=args.root,
        config_path=args.config,
        session_id=args.session or None,
        budgets=ChatBudgets(
            max_session_turns=args.max_turns,
            max_steps_per_turn=args.max_steps,
            max_tool_calls=args.max_tool_calls,
            max_estimated_tokens=token_budget,
        ),
    )


def _cmd_run(args: Any) -> int:
    run_task_with_failure_alerts(args.task_name, config_path=args.config)
    return 0


def _cmd_models(args: Any) -> int:
    from app.llm import supported_llm_providers
    from app.llm.models_cli import (
        add_fallback_model,
        check_model_health,
        format_models_status,
        set_default_model,
    )

    sub = getattr(args, "models_command", None)
    if sub in {None, "status"}:
        providers = ", ".join(sorted(supported_llm_providers()))
        print(f"supported_providers: {providers}")
        print(format_models_status(root=args.root, config_path=args.config))
        return 0
    if sub == "set":
        selected = set_default_model(
            args.model_id,
            root=args.root,
            config_path=args.config,
        )
        print(f"default model set to {selected}")
        return 0
    if sub == "fallback":
        fb = getattr(args, "models_fallback_command", None)
        if fb != "add":
            print(
                "Usage: signal-agent models fallback add <provider/model>",
                file=sys.stderr,
            )
            return 2
        items = add_fallback_model(
            args.model_id,
            root=args.root,
            config_path=args.config,
        )
        print("fallbacks:")
        for item in items:
            print(f"- {item}")
        return 0
    if sub == "test":
        result = check_model_health(root=args.root, config_path=args.config)
        if result["ok"]:
            print(f"models test: ok served={result['served']}")
            return 0
        print(
            "models test: failed "
            f"class={result.get('failure_class')} "
            f"error={result.get('error_type')}: {result.get('error')}",
            file=sys.stderr,
        )
        return 1
    print(f"Unknown models command: {sub}", file=sys.stderr)
    return 2


def _cmd_schedules(args: Any) -> int:
    from app.runtime import paths as runtime_paths
    from app.scheduler import ScheduleStore, format_schedules

    store = ScheduleStore(runtime_paths.db_path(args.root))
    print(format_schedules(store.list()))
    return 0


def _cmd_schedule(args: Any) -> int:
    from app.runtime import paths as runtime_paths
    from app.scheduler import ScheduleStore

    store = ScheduleStore(runtime_paths.db_path(args.root))
    handlers = {
        "add": _schedule_add,
        "enable": _schedule_enable,
        "disable": _schedule_disable,
        "run": _schedule_run,
        "history": _schedule_history,
    }
    sub = getattr(args, "schedule_command", None)
    handler = handlers.get(sub) if isinstance(sub, str) else None
    if handler is None:
        print(
            "Usage: signal-agent schedule add|enable|disable|run|history ...",
            file=sys.stderr,
        )
        return 2
    return handler(args, store=store)


def _schedule_add(args: Any, *, store: Any) -> int:
    from app.scheduler import format_schedules

    task_name = str(args.schedule_task)
    known = list_tasks()
    if task_name not in known:
        raise ValueError(
            f"Unknown task {task_name!r}; choose one of: {', '.join(known)}"
        )
    schedule = store.add(
        name=args.name,
        task_name=task_name,
        timezone=args.timezone,
        cron=args.cron or None,
        run_at=args.run_at or None,
        allow_overlap=bool(args.allow_overlap),
        dry_run=bool(args.dry_run),
        max_retries=args.max_retries,
        backoff_seconds=args.backoff_seconds,
    )
    print(format_schedules([schedule]))
    return 0


def _schedule_enable(args: Any, *, store: Any) -> int:
    from app.scheduler import format_schedules

    print(format_schedules([store.set_enabled(args.schedule_id, enabled=True)]))
    return 0


def _schedule_disable(args: Any, *, store: Any) -> int:
    from app.scheduler import format_schedules

    print(format_schedules([store.set_enabled(args.schedule_id, enabled=False)]))
    return 0


def _schedule_run(args: Any, **_kwargs: Any) -> int:
    from app.scheduler import run_schedule_now

    status = run_schedule_now(
        args.schedule_id,
        root=args.root,
        config_path=args.config,
        dry_run=bool(args.dry_run) or None,
    )
    print(f"schedule run: {status}")
    return 0 if status in {"started_ok", "skipped_disabled"} else 1


def _schedule_history(args: Any, *, store: Any) -> int:
    if store.get(args.schedule_id) is None:
        raise KeyError(args.schedule_id)
    rows = store.history(args.schedule_id, limit=args.limit)
    if not rows:
        print("(no runs)")
        return 0
    for row in rows:
        print(
            f"{row.run_id}  {row.status}  attempt={row.attempt}  "
            f"started={row.started_at}  finished={row.finished_at or '-'}  "
            f"error={row.error or '-'}"
        )
    return 0


def _cmd_tools(args: Any) -> int:
    from app.core.config import load_config
    from app.tools import DEFAULT_TOOL_ALLOWLIST, build_tool_registry

    try:
        config = load_config(args.config)
        # Preserve an explicit empty allowlist (operator disabled all tools).
        allowlist = list(config.tools.allowlist)
    except Exception:  # noqa: BLE001
        allowlist = list(DEFAULT_TOOL_ALLOWLIST)
    registry = build_tool_registry(allowlist=allowlist)
    if not allowlist:
        print("(none)")
        return 0
    for item in registry.list_tools():
        print(f"{item['name']}: {item['description']}")
    return 0


def _cmd_logs(args: Any) -> int:
    from app.runtime import format_log_lines, read_log_tail
    from app.runtime.paths import log_path

    if args.lines < 0:
        raise ValueError("--lines must be non-negative")
    if not args.follow:
        print(format_log_lines(read_log_tail(root=args.root, lines=args.lines)))
        return 0

    path = log_path(args.root)
    print(format_log_lines(read_log_tail(root=args.root, lines=args.lines)))
    offset = path.stat().st_size if path.is_file() else 0
    try:
        while True:
            time.sleep(0.5)
            if not path.is_file():
                continue
            size = path.stat().st_size
            if size < offset:
                offset = 0
            if size == offset:
                continue
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                handle.seek(offset)
                chunk = handle.read()
                offset = handle.tell()
            from app.runtime.logs import collect_secrets
            from app.tools.redaction import redact_value

            secrets = collect_secrets(args.root)
            sys.stdout.write(redact_value(chunk, secrets))
            sys.stdout.flush()
    except KeyboardInterrupt:
        print("", file=sys.stderr)
        return 0


# Re-exports used by older tests/monkeypatches.
__all__ = [
    "DEFAULT_CONFIG_PATH",
    "build_parser",
    "build_task",
    "main",
    "print_health_report",
    "print_recent_runs",
    "run_eval_command",
    "run_langchain_smoke_command",
    "run_mcp_serve_command",
    "run_task_with_failure_alerts",
    "send_telegram_test_message",
]
