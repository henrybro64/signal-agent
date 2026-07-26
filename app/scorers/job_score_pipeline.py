"""Two-stage job scoring: rule filter first, optional LLM refinement."""

from __future__ import annotations

from dataclasses import dataclass, replace

from app.core import SignalItem
from app.core.config import LlmConfig, ScoringConfig
from app.core.secrets import EnvSettings
from app.llm import create_llm_provider

from .job_llm_scorer import JobLLMScorer, JobLLMScorerError
from .job_rule_scorer import JobRuleScore, JobRuleScorer
from .job_search import load_job_search_profile


@dataclass(slots=True)
class LlmCallBudget:
    """Shared per-task-run budget for LLM scoring attempts."""

    max_calls: int
    _calls: int = 0

    @property
    def calls(self) -> int:
        return self._calls

    def remaining(self) -> int:
        return max(0, self.max_calls - self._calls)

    def try_consume(self) -> bool:
        """Reserve one LLM attempt when budget remains."""

        if self._calls >= self.max_calls:
            return False
        self._calls += 1
        return True


@dataclass(slots=True)
class JobScorePipelineResult:
    """Final score plus metadata about how it was produced."""

    score: JobRuleScore
    scoring_method: str
    llm_attempted: bool = False
    llm_skipped_reason: str | None = None
    llm_fallback_reason: str | None = None
    rule_score: int | None = None


class JobScorePipeline:
    """Run rule scoring first and optionally refine with the LLM."""

    def __init__(
        self,
        *,
        rule_scorer: JobRuleScorer,
        llm_scorer: JobLLMScorer | None,
        llm_enabled: bool,
        llm_min_rule_score: int,
        llm_budget: LlmCallBudget,
    ) -> None:
        self.rule_scorer = rule_scorer
        self.llm_scorer = llm_scorer
        self.llm_enabled = llm_enabled
        self.llm_min_rule_score = llm_min_rule_score
        self.llm_budget = llm_budget

    @property
    def llm_max_jobs_per_run(self) -> int:
        return self.llm_budget.max_calls

    def score(self, item: SignalItem) -> JobScorePipelineResult:
        rule_result = self.rule_scorer.score(item)

        if not self.llm_enabled or self.llm_scorer is None:
            return self._rule_only_result(rule_result)

        if rule_result.score < self.llm_min_rule_score:
            return self._rule_only_result(
                rule_result,
                llm_skipped_reason="below_min_rule_score",
            )

        if not self.llm_budget.try_consume():
            return self._rule_only_result(
                rule_result,
                llm_skipped_reason="max_jobs_per_run_reached",
            )

        try:
            llm_result = self.llm_scorer.score_with_llm(item)
        except JobLLMScorerError as exc:
            return JobScorePipelineResult(
                score=_annotate_score(rule_result, "rule_fallback", str(exc)),
                scoring_method="rule_fallback",
                llm_attempted=True,
                llm_fallback_reason=str(exc),
                rule_score=rule_result.score,
            )

        return JobScorePipelineResult(
            score=_annotate_score(llm_result, "llm"),
            scoring_method="llm",
            llm_attempted=True,
            rule_score=rule_result.score,
        )

    def _rule_only_result(
        self,
        rule_result: JobRuleScore,
        *,
        llm_skipped_reason: str | None = None,
    ) -> JobScorePipelineResult:
        return JobScorePipelineResult(
            score=_annotate_score(rule_result, "rule"),
            scoring_method="rule",
            llm_skipped_reason=llm_skipped_reason,
            rule_score=rule_result.score,
        )


def build_job_score_pipeline(
    *,
    config: ScoringConfig,
    env: EnvSettings,
    scorer_profile: str | None = None,
    llm_budget: LlmCallBudget | None = None,
    llm_config: LlmConfig | None = None,
) -> JobScorePipeline:
    """Build a configured two-stage scoring pipeline.

    Model selection uses ``llm.model`` (``LlmConfig.model``). When ``llm_config``
    is omitted, ``scoring.llm_model`` seeds the gateway defaults so unit tests
    and older call sites keep working. Loaded ``AppConfig`` keeps both in sync.
    """

    profile = load_job_search_profile(scorer_profile)
    rule_scorer = JobRuleScorer(profile)
    llm_scorer: JobLLMScorer | None = None
    gateway = llm_config or LlmConfig(model=config.llm_model)
    if config.llm_enabled:
        provider = create_llm_provider(
            provider=gateway.provider,
            api_key=env.openai_api_key or "",
            model=gateway.model,
            timeout_seconds=gateway.timeout_seconds,
            temperature=gateway.temperature,
        )
        llm_scorer = JobLLMScorer(
            provider=provider,
            model=gateway.model,
            profile=profile,
            fallback_scorer=rule_scorer,
            timeout_seconds=gateway.timeout_seconds,
            temperature=gateway.temperature,
        )
    budget = llm_budget or LlmCallBudget(max_calls=config.llm_max_jobs_per_run)
    return JobScorePipeline(
        rule_scorer=rule_scorer,
        llm_scorer=llm_scorer,
        llm_enabled=config.llm_enabled,
        llm_min_rule_score=config.llm_min_rule_score,
        llm_budget=budget,
    )


def _annotate_score(
    score: JobRuleScore,
    method: str,
    fallback_note: str | None = None,
) -> JobRuleScore:
    reason = f"[{method}] {score.reason}"
    if fallback_note:
        reason = f"{reason} LLM fallback: {fallback_note}."
    return replace(score, reason=reason)
