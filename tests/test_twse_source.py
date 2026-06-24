from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import requests

from app.core import SignalItem
from app.sources import DummySource, TwseSource, TwseSourceError, fetch_from_sources
from tests.source_fakes import FakeResponse, FakeSession

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "twse_sample.json"


def _load_fixture() -> dict[str, object]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_twse_source_fetch_normalizes_stock_movers() -> None:
    fixture = _load_fixture()
    session = FakeSession(
        [
            FakeResponse(json_payload=fixture["industry"]),
            FakeResponse(json_payload=fixture["category_24"]),
            FakeResponse(json_payload=fixture["stock_info"]),
        ]
    )
    source = TwseSource(
        session=session,
        request_delay_seconds=0.0,
    )

    items = source.fetch()

    assert len(items) == 2
    assert all(isinstance(item, SignalItem) for item in items)

    first_item = items[0]
    assert first_item.source == "twse"
    assert first_item.item_type == "tw_stock_mover"
    assert first_item.title == "TSMC 上漲 1.00%"
    assert first_item.external_id == "twse-20260623-0905-2330"
    assert first_item.url == (
        "https://www.twse.com.tw/zh/page/trading/exchange/STOCK_DAY.html"
        "?date=20260623&stockNo=2330&snapshot=0905"
    )
    assert first_item.observed_at == datetime.fromtimestamp(1782176700, tz=UTC)
    assert first_item.content == (
        "TWSE 2330 last 1010.00, prev close 1000.00, change +10.00 (+1.00%), "
        "volume 12,345,678, open 1005.00, high 1015.00, low 995.00."
    )
    assert first_item.metadata["stock_code"] == "2330"
    assert first_item.metadata["stock_name"] == "TSMC"
    assert first_item.metadata["company"] == "TSMC"
    assert first_item.metadata["market"] == "twse"
    assert first_item.metadata["exchange"] == "tse"
    assert first_item.metadata["price"] == 1010.0
    assert first_item.metadata["last_price"] == 1010.0
    assert first_item.metadata["change_amount"] == 10.0
    assert first_item.metadata["change_percent"] == 1.0
    assert first_item.metadata["volume"] == 12345678

    second_item = items[1]
    assert second_item.title == "MediaTek 下跌 2.00%"
    assert second_item.external_id == "twse-20260623-0905-2454"
    assert second_item.metadata["stock_code"] == "2454"
    assert second_item.metadata["price"] == 1470.0
    assert second_item.metadata["change_amount"] == -30.0
    assert second_item.metadata["change_percent"] == -2.0
    assert second_item.metadata["volume"] == 8765432


def test_twse_source_uses_query_time_for_snapshot_ids() -> None:
    session = FakeSession(
        [
            FakeResponse(
                json_payload={
                    "tse": [{"code": "24", "name": "Semiconductors"}]
                }
            ),
            FakeResponse(
                json_payload={
                    "msgArray": [{"ex": "tse", "ch": "2330.tw", "n": "TSMC"}]
                }
            ),
            FakeResponse(
                json_payload={
                    "rtcode": "0000",
                    "queryTime": {
                        "sysDate": "20260623",
                        "sysTime": "10:10:00",
                    },
                    "msgArray": [
                        {
                            "c": "2330",
                            "n": "TSMC",
                            "z": "1010",
                            "y": "1000",
                            "t": "09:00:00",
                            "d": "20260623",
                            "tlong": "1782173100000",
                        }
                    ],
                }
            ),
        ]
    )
    source = TwseSource(session=session, request_delay_seconds=0.0)

    items = source.fetch()

    assert len(items) == 1
    assert items[0].external_id == "twse-20260623-1010-2330"
    assert items[0].url is not None
    assert "snapshot=1010" in items[0].url


def test_twse_source_batches_quote_requests_and_skips_non_equity_categories() -> None:
    session = FakeSession(
        [
            FakeResponse(
                json_payload={
                    "tse": [
                        {"code": "24", "name": "Semiconductors"},
                        {"code": "B0", "name": "ETF"},
                        {"code": "E0", "name": "Electrical"},
                    ]
                }
            ),
            FakeResponse(
                json_payload={
                    "msgArray": [
                        {"ex": "tse", "ch": "2330.tw", "n": "TSMC"},
                        {"ex": "tse", "ch": "2454.tw", "n": "MediaTek"},
                    ]
                }
            ),
            FakeResponse(
                json_payload={
                    "msgArray": [
                        {"ex": "tse", "ch": "1101.tw", "n": "Taiwan Cement"},
                    ]
                }
            ),
            FakeResponse(
                json_payload={
                    "rtcode": "0000",
                    "msgArray": [
                        {
                            "c": "2330",
                            "n": "TSMC",
                            "z": "1010",
                            "y": "1000",
                            "d": "20260623",
                            "t": "09:05:00",
                        },
                        {
                            "c": "2454",
                            "n": "MediaTek",
                            "z": "1470",
                            "y": "1500",
                            "d": "20260623",
                            "t": "09:05:01",
                        },
                    ],
                }
            ),
            FakeResponse(
                json_payload={
                    "rtcode": "0000",
                    "msgArray": [
                        {
                            "c": "1101",
                            "n": "Taiwan Cement",
                            "z": "41.5",
                            "y": "40.0",
                            "d": "20260623",
                            "t": "09:05:02",
                        },
                    ],
                }
            ),
        ]
    )
    source = TwseSource(
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

    stock_calls = [
        call
        for call in session.calls
        if call["url"] == source.stock_info_url
    ]
    assert len(stock_calls) == 2
    assert stock_calls[0]["params"]["ex_ch"] == "tse_2330.tw|tse_2454.tw"
    assert stock_calls[1]["params"]["ex_ch"] == "tse_1101.tw"


def test_twse_source_retries_transient_quote_errors() -> None:
    session = FakeSession(
        [
            FakeResponse(
                json_payload={
                    "tse": [{"code": "24", "name": "Semiconductors"}]
                }
            ),
            FakeResponse(
                json_payload={
                    "msgArray": [{"ex": "tse", "ch": "2330.tw", "n": "TSMC"}]
                }
            ),
            FakeResponse(status_code=503, text="service unavailable"),
            FakeResponse(
                json_payload={
                    "rtcode": "0000",
                    "msgArray": [
                        {
                            "c": "2330",
                            "n": "TSMC",
                            "z": "1010",
                            "y": "1000",
                            "d": "20260623",
                            "t": "09:05:00",
                        }
                    ],
                }
            ),
        ]
    )
    source = TwseSource(
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


def test_twse_source_failure_can_be_skipped_in_source_pipeline() -> None:
    session = FakeSession([requests.RequestException("network down")])
    source = TwseSource(
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
    assert errors == ["twse: TWSE industry list request failed: network down"]


def test_twse_source_raises_for_rtcode_errors() -> None:
    session = FakeSession(
        [
            FakeResponse(
                json_payload={
                    "tse": [{"code": "24", "name": "Semiconductors"}]
                }
            ),
            FakeResponse(
                json_payload={
                    "msgArray": [{"ex": "tse", "ch": "2330.tw", "n": "TSMC"}]
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
    source = TwseSource(
        session=session,
        request_delay_seconds=0.0,
    )

    with pytest.raises(
        TwseSourceError,
        match="TWSE stock quotes returned rtcode error: busy",
    ):
        source.fetch()
