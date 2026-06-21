from app.core import SignalItem
from app.scorers import JobRuleScorer
from app.sources.dummy import SAMPLE_OBSERVED_AT


def test_job_rule_scorer_returns_strong_match_for_good_fit_job() -> None:
    scorer = JobRuleScorer()
    item = SignalItem(
        source="manual",
        item_type="job",
        title="Cloud SDET",
        observed_at=SAMPLE_OBSERVED_AT,
        content=(
            "Remote role building Python and Pytest automation for Azure, AWS, "
            "and Kubernetes workloads with SIEM, SOAR, DLP, and API security."
        ),
        metadata={
            "company": "Example Security Product Company",
            "location": "Taipei",
            "work_style": "Hybrid",
        },
    )

    result = scorer.score(item)

    assert result.score == 100
    assert result.recommendation == "strong_match"
    assert result.reasons
    assert result.concerns == []
    assert "Cloud SDET" in result.reason
    assert "no major concerns" in result.reason


def test_job_rule_scorer_returns_reject_for_bad_fit_job() -> None:
    scorer = JobRuleScorer()
    item = SignalItem(
        source="manual",
        item_type="job",
        title="Helpdesk Support Engineer",
        observed_at=SAMPLE_OBSERVED_AT,
        content=(
            "Customer support role with SLA-heavy support, rotating shift work, "
            "on-call-heavy support, SCCM, and Intune administration."
        ),
        metadata={"location": "Taipei"},
    )

    result = scorer.score(item)

    assert result.score == 0
    assert result.recommendation == "reject"
    assert result.reasons == ["Matched location preference: Taipei (+5)"]
    assert len(result.concerns) >= 4
    assert any("Helpdesk" in concern for concern in result.concerns)
    assert any("SLA-heavy support" in concern for concern in result.concerns)
    assert "reject (0/100)" in result.reason


def test_job_rule_scorer_returns_maybe_for_mixed_fit_job() -> None:
    scorer = JobRuleScorer()
    item = SignalItem(
        source="manual",
        item_type="job",
        title="Test Automation Engineer",
        observed_at=SAMPLE_OBSERVED_AT,
        content=(
            "Hybrid Python and Cypress automation role, but the team also runs "
            "an on-call-heavy support rotation."
        ),
    )

    result = scorer.score(item)

    assert 25 <= result.score < 60
    assert result.recommendation == "maybe"
    assert any("Python" in reason for reason in result.reasons)
    assert any("Cypress" in reason for reason in result.reasons)
    assert any(
        "on-call-heavy support" in concern.casefold()
        for concern in result.concerns
    )
