"""Additive LangGraph/plain-Python job research pilot task (#64).

Does not replace the deterministic ``job_search`` task.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.core import load_config, load_env_settings
from app.core.db import TaskRunStatus, finish_task_run, init_db, start_task_run
from app.core.health import resolve_db_path
from app.core.logging import get_logger, log_event
from app.llm import FakeLLMProvider, create_llm_provider
from app.tools import ToolContext, build_tool_registry
from app.workflows import JobResearchState, run_job_research

from .base import BaseTask

_LOGGER = get_logger("tasks.job_research_agent")


class JobResearchAgentTask(BaseTask):
    """Bounded agent-style job research digest (pilot)."""

    name = "job_research_agent"

    def run(self) -> None:  # skipcq: PY-R1000
        self._execute_research_run()

    def _execute_research_run(self) -> None:
        prepared = self._prepare()
        run_id = prepared["run_id"]
        db_path = prepared["db_path"]
        try:
            registry, context, provider = self._build_runtime(
                config=prepared["config"],
                env=prepared["env"],
                db_path=db_path,
                telegram_enabled=prepared["telegram_enabled"],
                allowlist=prepared["allowlist"],
            )
            state = JobResearchState(
                profile_name="default",
                task_run_id=run_id,
                config_path=self.config_path,
                telegram_enabled=prepared["telegram_enabled"],
                max_steps=prepared["task_cfg"].max_steps,
                max_tool_calls=prepared["task_cfg"].max_tool_calls,
                max_llm_calls=prepared["task_cfg"].max_llm_calls,
            )
            final = run_job_research(
                state,
                registry=registry,
                context=context,
                provider=provider,
                use_langgraph=None,
            )
            self._finish_success(run_id=run_id, db_path=db_path, final=final)
        except Exception as exc:
            self._finish_failed(run_id=run_id, db_path=db_path, error=exc)
            raise

    def _prepare(self) -> dict[str, Any]:
        config = load_config(self.config_path)
        task_cfg = config.tasks.job_research_agent
        if not task_cfg.enabled:
            raise ValueError("Task 'job_research_agent' is disabled in config")

        env = load_env_settings()
        db_path = resolve_db_path()
        init_db(db_path)

        telegram_enabled = bool(
            config.notifiers.telegram.enabled and task_cfg.telegram_enabled
        )
        allowlist = list(config.tools.allowlist)
        if telegram_enabled and "send_telegram_message" not in allowlist:
            allowlist.append("send_telegram_message")

        run_id = start_task_run(self.name, db_path=db_path)
        log_event(
            _LOGGER,
            event="task_started",
            message="Job research agent task started",
            task_run_id=run_id,
            db_path=str(db_path),
        )
        return {
            "config": config,
            "task_cfg": task_cfg,
            "env": env,
            "db_path": db_path,
            "telegram_enabled": telegram_enabled,
            "allowlist": allowlist,
            "run_id": run_id,
        }

    @staticmethod
    def _build_runtime(
        *,
        config: Any,
        env: Any,
        db_path: Path,
        telegram_enabled: bool,
        allowlist: list[str],
    ) -> tuple[Any, ToolContext, Any]:
        registry = build_tool_registry(
            allowlist=allowlist,
            include_telegram=telegram_enabled,
        )
        context = ToolContext(
            config=config,
            env=env,
            db_path=Path(db_path),
            telegram_enabled=telegram_enabled,
            timeout_seconds=config.tools.timeout_seconds,
            secrets_to_redact=tuple(
                value
                for value in (
                    env.openai_api_key,
                    env.telegram_bot_token,
                    env.telegram_chat_id,
                )
                if value
            ),
        )

        provider = None
        if config.scoring.llm_enabled and env.openai_api_key:
            provider = create_llm_provider(
                provider=config.llm.provider,
                api_key=env.openai_api_key,
                model=config.scoring.llm_model,
                timeout_seconds=config.llm.timeout_seconds,
                temperature=config.llm.temperature,
            )
        elif config.llm.provider == "fake":
            provider = FakeLLMProvider(
                responses=["Brief note: deterministic fake analysis."],
                validate_structured_json=False,
            )
        context.llm = provider
        return registry, context, provider

    @staticmethod
    def _finish_success(*, run_id: int, db_path: Path, final: Any) -> None:
        notified = 1 if final.notification_status == "sent" else 0
        run_status: TaskRunStatus = (
            "success" if final.status == "completed" else "failed"
        )
        if final.status == "completed_with_errors":
            run_status = "failed"
        error_message = None
        if final.errors:
            error_message = "; ".join(final.errors)[:500]
        finish_task_run(
            run_id,
            status=run_status,
            fetched_count=len(final.candidates),
            new_count=final.new_candidate_count or len(final.filtered),
            skipped_count=max(0, len(final.candidates) - len(final.filtered)),
            notified_count=notified,
            error_message=error_message,
            db_path=db_path,
        )
        log_event(
            _LOGGER,
            event="task_finished",
            message="Job research agent task finished",
            task_run_id=run_id,
            status=final.status,
            task_run_status=run_status,
            digest_chars=len(final.digest_text),
            fallback_used=final.fallback_used,
            persisted_count=final.persisted_count,
            new_candidate_count=final.new_candidate_count,
        )
        if final.digest_text:
            print(final.digest_text)

    @staticmethod
    def _finish_failed(*, run_id: int, db_path: Path, error: Exception) -> None:
        finish_task_run(
            run_id,
            status="failed",
            fetched_count=0,
            new_count=0,
            skipped_count=0,
            notified_count=0,
            error_message=str(error)[:500],
            db_path=db_path,
        )
