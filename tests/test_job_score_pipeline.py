from __future__ import annotations

from app.core import SignalItem
from app.core.config import ScoringConfig
from app.scorers import (
    JobLLMScorer,
    JobRuleScore,
    JobScorePipeline,
    LlmCallBudget,
    build_job_score_pipeline,
)
from app.scorers.job_llm_scorer import JobLLMScorerError
from tests.test_job_search_task import OBSERVED_AT


def test_pipeline_rule_only_when_llm_disabled() -> None:
    rule_scorer = _FakeRuleScorer({"good": 80, "bad": 10})
    pipeline = JobScorePipeline(
        rule_scorer=rule_scorer,
        llm_scorer=None,
        llm_enabled=False,
        llm_min_rule_score=25,
        llm_budget=LlmCallBudget(max_calls=20),
    )

    result = pipeline.score(_job("good"))

    assert result.scoring_method == "rule"
    assert result.llm_attempted is False
    assert result.score.score == 80
    assert result.score.reason.startswith("[rule]")


def test_pipeline_skips_llm_below_min_rule_score() -> None:
    rule_scorer = _FakeRuleScorer({"bad": 10})
    llm_scorer = _FakeLLMScorer(score=90)
    pipeline = JobScorePipeline(
        rule_scorer=rule_scorer,
        llm_scorer=llm_scorer,
        llm_enabled=True,
        llm_min_rule_score=25,
        llm_budget=LlmCallBudget(max_calls=20),
    )

    result = pipeline.score(_job("bad"))

    assert result.scoring_method == "rule"
    assert result.llm_skipped_reason == "below_min_rule_score"
    assert llm_scorer.calls == 0


def test_pipeline_calls_llm_for_plausible_candidates() -> None:
    rule_scorer = _FakeRuleScorer({"good": 80})
    llm_scorer = _FakeLLMScorer(score=88)
    pipeline = JobScorePipeline(
        rule_scorer=rule_scorer,
        llm_scorer=llm_scorer,
        llm_enabled=True,
        llm_min_rule_score=25,
        llm_budget=LlmCallBudget(max_calls=20),
    )

    result = pipeline.score(_job("good"))

    assert result.scoring_method == "llm"
    assert result.llm_attempted is True
    assert result.rule_score == 80
    assert result.score.score == 88
    assert result.score.reason.startswith("[llm]")
    assert llm_scorer.calls == 1


def test_pipeline_enforces_llm_max_jobs_per_run() -> None:
    rule_scorer = _FakeRuleScorer({"a": 80, "b": 70, "c": 60})
    llm_scorer = _FakeLLMScorer(score=90)
    pipeline = JobScorePipeline(
        rule_scorer=rule_scorer,
        llm_scorer=llm_scorer,
        llm_enabled=True,
        llm_min_rule_score=25,
        llm_budget=LlmCallBudget(max_calls=2),
    )

    first = pipeline.score(_job("a"))
    second = pipeline.score(_job("b"))
    third = pipeline.score(_job("c"))

    assert first.scoring_method == "llm"
    assert second.scoring_method == "llm"
    assert third.scoring_method == "rule"
    assert third.llm_skipped_reason == "max_jobs_per_run_reached"
    assert llm_scorer.calls == 2


def test_shared_llm_budget_is_enforced_across_pipelines() -> None:
    budget = LlmCallBudget(max_calls=2)
    llm_scorer = _FakeLLMScorer(score=90)
    first = JobScorePipeline(
        rule_scorer=_FakeRuleScorer({"a": 80}),
        llm_scorer=llm_scorer,
        llm_enabled=True,
        llm_min_rule_score=25,
        llm_budget=budget,
    )
    second = JobScorePipeline(
        rule_scorer=_FakeRuleScorer({"b": 70, "c": 60}),
        llm_scorer=llm_scorer,
        llm_enabled=True,
        llm_min_rule_score=25,
        llm_budget=budget,
    )

    assert first.score(_job("a")).scoring_method == "llm"
    assert second.score(_job("b")).scoring_method == "llm"
    skipped = second.score(_job("c"))

    assert skipped.scoring_method == "rule"
    assert skipped.llm_skipped_reason == "max_jobs_per_run_reached"
    assert budget.calls == 2
    assert llm_scorer.calls == 2


def test_pipeline_falls_back_to_rule_score_when_llm_fails() -> None:
    rule_scorer = _FakeRuleScorer({"good": 80})
    llm_scorer = _FailingLLMScorer()
    pipeline = JobScorePipeline(
        rule_scorer=rule_scorer,
        llm_scorer=llm_scorer,
        llm_enabled=True,
        llm_min_rule_score=25,
        llm_budget=LlmCallBudget(max_calls=20),
    )

    result = pipeline.score(_job("good"))

    assert result.scoring_method == "rule_fallback"
    assert result.llm_attempted is True
    assert result.llm_fallback_reason == "LLM request failed"
    assert result.score.score == 80
    assert result.score.reason.startswith("[rule_fallback]")
    assert "LLM fallback: LLM request failed." in result.score.reason


def test_build_job_score_pipeline_from_config() -> None:
    config = ScoringConfig(
        freshness_weight=0.6,
        priority_weight=0.4,
        llm_enabled=True,
        llm_model="gpt-5.2",
        llm_min_rule_score=30,
        llm_max_jobs_per_run=5,
    )

    pipeline = build_job_score_pipeline(
        config=config,
        env=_env_with_key(),
        scorer_profile=None,
    )

    assert pipeline.llm_enabled is True
    assert pipeline.llm_min_rule_score == 30
    assert pipeline.llm_max_jobs_per_run == 5
    assert pipeline.llm_scorer is not None
    assert isinstance(pipeline.llm_scorer, JobLLMScorer)
    assert pipeline.llm_scorer.model == "gpt-5.2"
    assert pipeline.llm_scorer.provider.name == "openai"
    assert not hasattr(pipeline.llm_scorer, "api_key")

    from app.core.config import LlmConfig

    gateway_pipeline = build_job_score_pipeline(
        config=config,
        env=_env_with_key(),
        llm_config=LlmConfig(model="gpt-from-gateway"),
    )
    assert gateway_pipeline.llm_scorer is not None
    assert gateway_pipeline.llm_scorer.model == "gpt-from-gateway"


class _FakeRuleScorer:
    def __init__(self, scores: dict[str, int]) -> None:
        self.scores = scores

    def score(self, item: SignalItem) -> JobRuleScore:
        value = self.scores[item.title]
        if value >= 60:
            recommendation = "strong_match"
        elif value >= 25:
            recommendation = "maybe"
        else:
            recommendation = "reject"
        return JobRuleScore(
            score=value,
            recommendation=recommendation,
            reason=f"{recommendation} ({value}/100): positives: test.",
        )


class _FakeLLMScorer:
    def __init__(self, *, score: int) -> None:
        self.score_value = score
        self.calls = 0

    def score_with_llm(self, item: SignalItem) -> JobRuleScore:
        self.calls += 1
        return JobRuleScore(
            score=self.score_value,
            recommendation="strong_match",
            reason=f"strong match ({self.score_value}/100): positives: LLM fit.",
        )


class _FailingLLMScorer:
    def score_with_llm(self, item: SignalItem) -> JobRuleScore:
        raise JobLLMScorerError("LLM request failed")


def _job(title: str) -> SignalItem:
    return SignalItem(
        source="static_jobs",
        item_type="job",
        title=title,
        observed_at=OBSERVED_AT,
        content="Python automation role.",
    )


def _env_with_key():
    from app.core.secrets import EnvSettings

    return EnvSettings(openai_api_key="sk-test")
