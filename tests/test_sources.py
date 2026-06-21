import pytest

from app.core import AppConfig, SignalItem
from app.sources import (
    BaseSource,
    DummySource,
    GlassdoorSource,
    IndeedSource,
    Job104Source,
    LinkedInEmailSource,
    build_job_search_sources,
    fetch_from_sources,
    list_source_names,
)
from app.sources.dummy import SAMPLE_OBSERVED_AT


def test_dummy_source_fetch_returns_signal_items() -> None:
    source = DummySource()

    items = source.fetch()

    assert source.name == "dummy"
    assert len(items) == 2
    assert all(isinstance(item, SignalItem) for item in items)
    assert items[0].source == "dummy"
    assert items[0].item_type == "job"
    assert items[1].item_type == "tw_stock_mover"


def test_fetch_from_sources_flattens_pipeline_results() -> None:
    items = fetch_from_sources([DummySource(), DummySource()])

    assert len(items) == 4
    assert [item.source for item in items] == ["dummy", "dummy", "dummy", "dummy"]


def test_fetch_from_sources_accepts_any_base_source() -> None:
    class SingleItemSource(BaseSource):
        name = "single"

        def fetch(self) -> list[SignalItem]:
            return [
                SignalItem(
                    source=self.name,
                    item_type="job",
                    title="Single item",
                    observed_at=SAMPLE_OBSERVED_AT,
                )
            ]

    items = fetch_from_sources([SingleItemSource()])

    assert len(items) == 1
    assert items[0].source == "single"


def test_fetch_from_sources_propagates_source_errors() -> None:
    class FailingSource(BaseSource):
        name = "failing"

        def fetch(self) -> list[SignalItem]:
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        fetch_from_sources([FailingSource()])


def test_fetch_from_sources_can_skip_failed_sources() -> None:
    class FailingSource(BaseSource):
        name = "failing"

        def fetch(self) -> list[SignalItem]:
            raise RuntimeError("boom")

    errors: list[str] = []

    def on_error(source: BaseSource, exc: Exception) -> None:
        errors.append(f"{source.name}: {exc}")

    items = fetch_from_sources(
        [DummySource(), FailingSource()],
        on_error=on_error,
    )

    assert len(items) == 2
    assert errors == ["failing: boom"]


def test_list_source_names_returns_supported_sources() -> None:
    assert list_source_names() == [
        "dummy",
        "glassdoor",
        "indeed",
        "job104",
        "linkedin_email",
    ]


def test_build_job_search_sources_builds_configured_sources() -> None:
    config = _build_config(
        ["dummy", "job104", "indeed", "glassdoor", "linkedin_email"]
    )

    sources = build_job_search_sources(config)

    assert [source.name for source in sources] == [
        "dummy",
        "job104",
        "indeed",
        "glassdoor",
        "linkedin_email",
    ]
    assert isinstance(sources[0], DummySource)
    assert isinstance(sources[1], Job104Source)
    assert isinstance(sources[2], IndeedSource)
    assert isinstance(sources[3], GlassdoorSource)
    assert isinstance(sources[4], LinkedInEmailSource)


def test_build_job_search_sources_can_skip_unknown_sources() -> None:
    config = _build_config(["dummy", "missing_source"])
    skipped: list[str] = []

    sources = build_job_search_sources(
        config,
        on_unknown=skipped.append,
    )

    assert [source.name for source in sources] == ["dummy"]
    assert skipped == ["missing_source"]


def test_build_job_search_sources_rejects_unknown_sources_by_default() -> None:
    config = _build_config(["missing_source"])

    with pytest.raises(
        ValueError,
        match="Unsupported source: missing_source",
    ):
        build_job_search_sources(config)


def _build_config(source_names: list[str]) -> AppConfig:
    return AppConfig.from_dict(
        {
            "agent": {
                "timezone": "Asia/Taipei",
                "digest_time": "20:00",
            },
            "tasks": {
                "job_search": {
                    "enabled": True,
                    "sources": source_names,
                    "keywords": ["python"],
                },
                "tw_stock_movers": {
                    "enabled": True,
                    "top_n": 20,
                    "timezone": "Asia/Taipei",
                },
            },
            "notifiers": {
                "telegram": {
                    "enabled": True,
                }
            },
            "scoring": {
                "freshness_weight": 0.6,
                "priority_weight": 0.4,
            },
        }
    )
