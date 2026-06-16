from datetime import UTC, datetime

from app.core import SignalItem

OBSERVED_AT = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)


def test_signal_item_defaults() -> None:
    item = SignalItem(
        source="manual",
        item_type="job",
        title="Backend Engineer",
        observed_at=OBSERVED_AT,
    )

    assert item.source == "manual"
    assert item.item_type == "job"
    assert item.title == "Backend Engineer"
    assert item.url is None
    assert item.external_id is None
    assert item.content is None
    assert item.metadata == {}
    assert item.observed_at == OBSERVED_AT


def test_signal_item_job_metadata() -> None:
    item = SignalItem(
        source="linkedin",
        item_type="job",
        title="Python Engineer",
        observed_at=OBSERVED_AT,
        external_id="li-123",
        url="https://www.linkedin.com/jobs/view/123",
        metadata={
            "company": "OpenAI",
            "location": "Taipei",
            "salary": "NT$1,800,000",
            "employment_type": "full-time",
        },
    )

    assert item.metadata["company"] == "OpenAI"
    assert item.metadata["location"] == "Taipei"
    assert item.metadata["salary"] == "NT$1,800,000"
    assert item.metadata["employment_type"] == "full-time"


def test_signal_item_stock_mover_metadata() -> None:
    item = SignalItem(
        source="twse",
        item_type="tw_stock_mover",
        title="TSMC rises 4.2%",
        observed_at=OBSERVED_AT,
        external_id="2330-2026-06-15",
        metadata={
            "stock_code": "2330",
            "company": "TSMC",
            "price": 1045.0,
            "change_percent": 4.2,
            "direction": "up",
        },
    )

    assert item.metadata["stock_code"] == "2330"
    assert item.metadata["company"] == "TSMC"
    assert item.metadata["price"] == 1045.0
    assert item.metadata["change_percent"] == 4.2
    assert item.metadata["direction"] == "up"


def test_signal_item_metadata_uses_new_dict_per_instance() -> None:
    first = SignalItem(
        source="manual", item_type="job", title="First", observed_at=OBSERVED_AT
    )
    second = SignalItem(
        source="manual", item_type="job", title="Second", observed_at=OBSERVED_AT
    )

    first.metadata["seen"] = True

    assert second.metadata == {}
