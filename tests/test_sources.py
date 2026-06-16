from app.core import SignalItem
from app.sources import BaseSource, DummySource, fetch_from_sources


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
                    observed_at=DummySource().fetch()[0].observed_at,
                )
            ]

    items = fetch_from_sources([SingleItemSource()])

    assert len(items) == 1
    assert items[0].source == "single"
