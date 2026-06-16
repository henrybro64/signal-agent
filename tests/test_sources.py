import pytest

from app.core import SignalItem
from app.sources import BaseSource, DummySource, fetch_from_sources
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
