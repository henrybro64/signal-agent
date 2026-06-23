from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import requests

from app.core import SignalItem
from app.sources import DummySource, TpexSource, TpexSourceError, fetch_from_sources
from tests.source_fakes import FakeResponse, FakeSession

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "tpex_sample.json"


def _load_fixture() -> dict[str, object]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_tpex_source_fetch_normalizes_stock_movers() -> None:
    fixture = _load_fixture()
    session = FakeSession(
        [
            FakeResponse(json_payload=fixture["industry"]),
            FakeResponse(json_payload=fixture["category_24"]),
            FakeResponse(json_payload=fixture["stock_info"]),
        ]
    )
    source = TpexSource(
        session=session,
        request_delay_seconds=0.0,
    )

    items = source.fetch()

    assert len(items) == 2
    assert all(isinstance(item, SignalItem) for item in items)

    first_item = items[0]
    assert first_item.source == "tpex"
    assert first_item.item_type == "tw_stock_mover"
    assert first_item.title == "穩懋 gains 3.51%"
    assert first_item.external_id == "tpex-20260623-3105"
    assert first_item.observed_at == datetime.fromtimestamp(1782196199, tz=UTC)
    assert first_item.content == (
        "TPEx 3105 last 560.00, prev close 541.00, change +19.00 (+3.51%), "
        "volume 24,567, open 545.00, high 561.00, low 543.00."
    )
    assert first_item.metadata["market"] == "tpex"
    assert first_item.metadata["exchange"] == "otc"
    assert first_item.metadata["stock_code"] == "3105"
    assert first_item.metadata["stock_name"] == "穩懋"
    assert first_item.metadata["company"] == "穩懋"
    assert first_item.metadata["price"] == 560.0
    assert first_item.metadata["change_amount"] == 19.0
    assert first_item.metadata["change_percent"] == 3.512
    assert first_item.metadata["volume"] == 24567

    second_item = items[1]
    assert second_item.title == "威剛 falls 5.07%"
    assert second_item.metadata["stock_code"] == "3260"
    assert second_item.metadata["price"] == 412.0
    assert second_item.metadata["change_amount"] == -22.0
    assert second_item.metadata["change_percent"] == -5.0691
    assert second_item.metadata["volume"] == 13449


def test_tpex_source_batches_quote_requests_and_skips_non_equity_categories() -> None:
    session = FakeSession(
        [
            FakeResponse(
                json_payload={
                    "otc": [
                        {"code": "24", "name": "Semiconductors"},
                        {"code": "B0", "name": "ETF"},
                        {"code": "E0", "name": "Electrical"},
                    ]
                }
            ),
            FakeResponse(
                json_payload={
                    "msgArray": [
                        {"ex": "otc", "ch": "3105.tw", "n": "穩懋"},
                        {"ex": "otc", "ch": "3260.tw", "n": "威剛"},
                    ]
                }
            ),
            FakeResponse(
                json_payload={
                    "msgArray": [
                        {"ex": "otc", "ch": "6015.tw", "n": "宏遠證"},
                    ]
                }
            ),
            FakeResponse(
                json_payload={
                    "rtcode": "0000",
                    "msgArray": [
                        {
                            "c": "3105",
                            "n": "穩懋",
                            "z": "560.0000",
                            "y": "541.0000",
                            "d": "20260623",
                            "t": "13:29:59",
                        },
                        {
                            "c": "3260",
                            "n": "威剛",
                            "z": "412.0000",
                            "y": "434.0000",
                            "d": "20260623",
                            "t": "13:30:00",
                        },
                    ],
                }
            ),
            FakeResponse(
                json_payload={
                    "rtcode": "0000",
                    "msgArray": [
                        {
                            "c": "6015",
                            "n": "宏遠證",
                            "z": "14.60",
                            "y": "14.00",
                            "d": "20260623",
                            "t": "13:29:58",
                        },
                    ],
                }
            ),
        ]
    )
    source = TpexSource(
        session=session,
        batch_size=2,
        request_delay_seconds=0.0,
    )

    items = source.fetch()

    assert len(items) == 3
    category_calls = [
        call
        for call in session.calls
        if call["url"] == source.category_url
    ]
    assert [call["params"]["i"] for call in category_calls] == ["24", "E0"]
    assert all(call["params"]["ex"] == "otc" for call in category_calls)

    stock_calls = [
        call
        for call in session.calls
        if call["url"] == source.stock_info_url
    ]
    assert len(stock_calls) == 2
    assert stock_calls[0]["params"]["ex_ch"] == "otc_3105.tw|otc_3260.tw"
    assert stock_calls[1]["params"]["ex_ch"] == "otc_6015.tw"


def test_tpex_source_retries_transient_quote_errors() -> None:
    session = FakeSession(
        [
            FakeResponse(
                json_payload={
                    "otc": [{"code": "24", "name": "Semiconductors"}]
                }
            ),
            FakeResponse(
                json_payload={
                    "msgArray": [{"ex": "otc", "ch": "3260.tw", "n": "威剛"}]
                }
            ),
            FakeResponse(status_code=503, text="service unavailable"),
            FakeResponse(
                json_payload={
                    "rtcode": "0000",
                    "msgArray": [
                        {
                            "c": "3260",
                            "n": "威剛",
                            "z": "412.0000",
                            "y": "434.0000",
                            "d": "20260623",
                            "t": "13:30:00",
                        }
                    ],
                }
            ),
        ]
    )
    source = TpexSource(
        session=session,
        max_retries=1,
        request_delay_seconds=0.0,
    )

    items = source.fetch()

    assert len(items) == 1
    stock_calls = [
        call
        for call in session.calls
        if call["url"] == source.stock_info_url
    ]
    assert len(stock_calls) == 2


def test_tpex_source_failure_can_be_skipped_in_source_pipeline() -> None:
    session = FakeSession([requests.RequestException("network down")])
    source = TpexSource(
        session=session,
        max_retries=0,
        request_delay_seconds=0.0,
    )
    errors: list[str] = []

    def on_error(source_name: object, exc: Exception) -> None:
        errors.append(f"{getattr(source_name, 'name', 'unknown')}: {exc}")

    items = fetch_from_sources(
        [DummySource(), source],
        on_error=on_error,
    )

    assert len(items) == 2
    assert errors == ["tpex: TPEx industry list request failed: network down"]


def test_tpex_source_raises_for_rtcode_errors() -> None:
    session = FakeSession(
        [
            FakeResponse(
                json_payload={
                    "otc": [{"code": "24", "name": "Semiconductors"}]
                }
            ),
            FakeResponse(
                json_payload={
                    "msgArray": [{"ex": "otc", "ch": "3260.tw", "n": "威剛"}]
                }
            ),
            FakeResponse(
                json_payload={
                    "rtcode": "9999",
                    "rtmessage": "busy",
                }
            ),
        ]
    )
    source = TpexSource(
        session=session,
        request_delay_seconds=0.0,
    )

    with pytest.raises(
        TpexSourceError,
        match="TPEx stock quotes returned rtcode error: busy",
    ):
        source.fetch()
