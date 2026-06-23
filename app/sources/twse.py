"""TWSE live stock-mover source."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, ClassVar
from zoneinfo import ZoneInfo

import requests

from app.core import SignalItem
from app.core.config import AppConfig

from .base import BaseSource

_DEFAULT_INDUSTRY_URL = "https://mis.twse.com.tw/stock/api/getIndustry.jsp"
_DEFAULT_CATEGORY_URL = "https://mis.twse.com.tw/stock/api/getCategory.jsp"
_DEFAULT_STOCK_INFO_URL = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
_DEFAULT_REQUEST_DELAY_SECONDS = 0.2
_REQUEST_TIMEOUT_SECONDS = 10
_TRANSIENT_STATUS_CODES = {429, 500, 502, 503, 504}
_EQUITY_LIKE_CATEGORY_CODES = {"E0", "H0", "Q0"}
_TAIPEI = ZoneInfo("Asia/Taipei")
_DEFAULT_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://mis.twse.com.tw/stock/index",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/137.0.0.0 Safari/537.36"
    ),
}


class TwseSourceError(RuntimeError):
    """Raised when the TWSE stock-mover source cannot fetch or normalize data."""


@dataclass(slots=True, frozen=True)
class _IndustryCategory:
    code: str
    name: str


@dataclass(slots=True, frozen=True)
class _CategoryMember:
    code: str
    channel: str
    exchange: str
    name: str
    industry_code: str
    industry_name: str


@dataclass(slots=True)
class TwseSource(BaseSource):
    """Fetch TWSE listed-stock movers from official live market APIs."""

    session: requests.Session | None = None
    request_delay_seconds: float = _DEFAULT_REQUEST_DELAY_SECONDS
    max_retries: int = 2
    batch_size: int = 100
    industry_url: str = _DEFAULT_INDUSTRY_URL
    category_url: str = _DEFAULT_CATEGORY_URL
    stock_info_url: str = _DEFAULT_STOCK_INFO_URL
    sleeper: Callable[[float], None] = time.sleep
    language: str = "zh_tw"

    name: ClassVar[str] = "twse"

    def __post_init__(self) -> None:
        if self.request_delay_seconds < 0:
            raise ValueError("request_delay_seconds must be zero or greater")
        if self.max_retries < 0:
            raise ValueError("max_retries must be zero or greater")
        if self.batch_size <= 0:
            raise ValueError("batch_size must be greater than zero")

    @classmethod
    def from_config(
        cls,
        _config: AppConfig,
        *,
        session: requests.Session | None = None,
        request_delay_seconds: float = _DEFAULT_REQUEST_DELAY_SECONDS,
        max_retries: int = 2,
        batch_size: int = 100,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> TwseSource:
        """Build the source from application config."""

        return cls(
            session=session,
            request_delay_seconds=request_delay_seconds,
            max_retries=max_retries,
            batch_size=batch_size,
            sleeper=sleeper,
        )

    def fetch(self) -> list[SignalItem]:
        session = self.session
        owns_session = session is None
        if owns_session:
            session = requests.Session()
            if hasattr(session, "headers"):
                session.headers.update(_DEFAULT_HEADERS)

        assert session is not None
        should_delay = False

        try:
            industries = self._fetch_industries(
                session=session,
                should_delay=should_delay,
            )
            should_delay = True
            if not industries:
                return []

            members: dict[str, _CategoryMember] = {}
            for industry in industries:
                category_members = self._fetch_category_members(
                    session=session,
                    industry=industry,
                    should_delay=should_delay,
                )
                should_delay = True
                for member in category_members:
                    members.setdefault(member.code, member)

            if not members:
                return []

            items: list[SignalItem] = []
            member_list = list(members.values())
            for batch in _batched(member_list, size=self.batch_size):
                payload = self._fetch_stock_info(
                    session=session,
                    members=batch,
                    should_delay=should_delay,
                )
                should_delay = True
                items.extend(self._normalize_quotes(payload, batch))

            return items
        finally:
            if owns_session and hasattr(session, "close"):
                session.close()

    def _fetch_industries(
        self,
        *,
        session: requests.Session,
        should_delay: bool,
    ) -> list[_IndustryCategory]:
        payload = self._request_json(
            session=session,
            url=self.industry_url,
            params={},
            context="TWSE industry list",
            should_delay=should_delay,
        )
        industry_rows = payload.get("tse")
        if not isinstance(industry_rows, list):
            raise TwseSourceError("TWSE industry response did not include a tse list")

        categories: list[_IndustryCategory] = []
        for row in industry_rows:
            if not isinstance(row, dict):
                continue
            code = _clean_text(row.get("code"))
            name = _clean_text(row.get("name"))
            if not code or not name or not _is_equity_like_category_code(code):
                continue
            categories.append(_IndustryCategory(code=code, name=name))
        return categories

    def _fetch_category_members(
        self,
        *,
        session: requests.Session,
        industry: _IndustryCategory,
        should_delay: bool,
    ) -> list[_CategoryMember]:
        payload = self._request_json(
            session=session,
            url=self.category_url,
            params={
                "ex": "tse",
                "i": industry.code,
                "lang": self.language,
            },
            context=f"TWSE category {industry.code}",
            should_delay=should_delay,
        )
        rows = payload.get("msgArray")
        if not isinstance(rows, list):
            raise TwseSourceError(
                f"TWSE category {industry.code} did not include a msgArray"
            )

        members: list[_CategoryMember] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            exchange = _clean_text(row.get("ex"))
            channel = _clean_text(row.get("ch"))
            name = _clean_text(row.get("n"))
            code = channel.split(".", 1)[0].strip() if channel else ""
            if not exchange or not channel or not code or not name:
                continue
            members.append(
                _CategoryMember(
                    code=code,
                    channel=channel,
                    exchange=exchange,
                    name=name,
                    industry_code=industry.code,
                    industry_name=industry.name,
                )
            )
        return members

    def _fetch_stock_info(
        self,
        *,
        session: requests.Session,
        members: list[_CategoryMember],
        should_delay: bool,
    ) -> dict[str, Any]:
        ex_ch = "|".join(
            f"{member.exchange}_{member.channel}"
            for member in members
        )
        return self._request_json(
            session=session,
            url=self.stock_info_url,
            params={
                "ex_ch": ex_ch,
                "json": "1",
                "delay": "0",
                "lang": self.language,
            },
            context="TWSE stock quotes",
            should_delay=should_delay,
        )

    def _request_json(
        self,
        *,
        session: requests.Session,
        url: str,
        params: dict[str, str],
        context: str,
        should_delay: bool,
    ) -> dict[str, Any]:
        if should_delay:
            self.sleeper(self.request_delay_seconds)

        for attempt in range(self.max_retries + 1):
            try:
                response = session.get(
                    url,
                    params=params,
                    timeout=_REQUEST_TIMEOUT_SECONDS,
                )
            except requests.RequestException as exc:
                if attempt >= self.max_retries:
                    raise TwseSourceError(
                        f"{context} request failed: {exc}"
                    ) from exc
                self._sleep_for_retry(attempt)
                continue

            if (
                response.status_code in _TRANSIENT_STATUS_CODES
                and attempt < self.max_retries
            ):
                self._sleep_for_retry(attempt)
                continue

            if response.status_code >= 400:
                raise TwseSourceError(
                    f"{context} failed with status {response.status_code}"
                )

            try:
                payload = response.json()
            except ValueError as exc:
                raise TwseSourceError(
                    f"{context} returned non-JSON content"
                ) from exc

            if not isinstance(payload, dict):
                raise TwseSourceError(f"{context} returned an unexpected payload")

            rtcode = _payload_status_code(payload)
            if rtcode and rtcode != "0000":
                message = _payload_status_message(payload) or "unknown error"
                raise TwseSourceError(f"{context} returned rtcode error: {message}")

            return payload

        raise TwseSourceError(f"{context} exhausted retries")

    def _sleep_for_retry(self, attempt: int) -> None:
        self.sleeper(self.request_delay_seconds * (attempt + 1))

    def _normalize_quotes(
        self,
        payload: dict[str, Any],
        members: list[_CategoryMember],
    ) -> list[SignalItem]:
        rows = payload.get("msgArray")
        if not isinstance(rows, list):
            raise TwseSourceError("TWSE stock quotes did not include a msgArray")

        query_time = payload.get("queryTime")
        members_by_code = {
            member.code: member
            for member in members
        }

        items: list[SignalItem] = []
        for row in rows:
            if not isinstance(row, dict):
                continue

            code = _clean_text(row.get("c"))
            member = members_by_code.get(code)
            if not code or member is None:
                continue

            last_price = _first_numeric(row.get("z"), row.get("pz"))
            previous_close = _parse_float(row.get("y"))
            if last_price is None or previous_close is None or previous_close <= 0:
                continue

            price_change = round(last_price - previous_close, 4)
            if abs(price_change) < 1e-9:
                continue
            change_percent = round((price_change / previous_close) * 100, 4)

            short_name = _clean_text(row.get("n")) or member.name
            full_name = _clean_text(row.get("nf")) or short_name
            if not short_name:
                continue

            observed_at = _parse_observed_at(
                row=row,
                query_time=query_time if isinstance(query_time, dict) else None,
            )
            trade_date = _clean_text(row.get("d"))
            effective_trade_date = trade_date or observed_at.astimezone(
                _TAIPEI
            ).strftime("%Y%m%d")
            direction = _direction_from_change(price_change)
            open_price = _parse_float(row.get("o"))
            high_price = _parse_float(row.get("h"))
            low_price = _parse_float(row.get("l"))
            volume = _parse_int(row.get("v"))
            last_trade_volume = _parse_int(row.get("tv"))

            items.append(
                SignalItem(
                    source=self.name,
                    item_type="tw_stock_mover",
                    title=_build_title(short_name, change_percent),
                    observed_at=observed_at,
                    url=_build_stock_url(
                        code=code,
                        trade_date=trade_date,
                        observed_at=observed_at,
                    ),
                    external_id=f"twse-{effective_trade_date}-{code}",
                    content=_build_content(
                        code=code,
                        last_price=last_price,
                        previous_close=previous_close,
                        price_change=price_change,
                        change_percent=change_percent,
                        volume=volume,
                        open_price=open_price,
                        high_price=high_price,
                        low_price=low_price,
                    ),
                    metadata=_compact_metadata(
                        {
                            "company": short_name,
                            "company_full_name": full_name,
                            "stock_name": short_name,
                            "stock_code": code,
                            "industry_code": member.industry_code,
                            "industry_name": member.industry_name,
                            "market": "twse",
                            "exchange": "tse",
                            "price": round(last_price, 4),
                            "last_price": round(last_price, 4),
                            "previous_close": round(previous_close, 4),
                            "change_amount": round(price_change, 4),
                            "price_change": round(price_change, 4),
                            "change_percent": round(change_percent, 4),
                            "direction": direction,
                            "open_price": open_price,
                            "high_price": high_price,
                            "low_price": low_price,
                            "volume": volume,
                            "last_trade_volume": last_trade_volume,
                            "quote_time": _clean_text(row.get("t")),
                        }
                    ),
                )
            )

        return items


def _batched(
    items: list[_CategoryMember],
    *,
    size: int,
) -> list[list[_CategoryMember]]:
    return [
        items[index:index + size]
        for index in range(0, len(items), size)
    ]


def _is_equity_like_category_code(code: str) -> bool:
    return code.isdigit() or code in _EQUITY_LIKE_CATEGORY_CODES


def _clean_text(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    return ""


def _parse_float(value: object) -> float | None:
    if isinstance(value, int | float):
        return float(value)
    if not isinstance(value, str):
        return None

    cleaned = value.strip().replace(",", "")
    if not cleaned or cleaned in {"-", "--"}:
        return None

    try:
        return float(cleaned)
    except ValueError:
        return None


def _parse_int(value: object) -> int | None:
    parsed = _parse_float(value)
    if parsed is None:
        return None
    return int(parsed)


def _payload_status_code(payload: dict[str, Any]) -> str:
    for key in ("rtcode", "rtCode", "retCode"):
        value = _clean_text(payload.get(key))
        if value:
            return value
    return ""


def _payload_status_message(payload: dict[str, Any]) -> str:
    for key in ("rtmessage", "rtMessage", "retMsg"):
        value = _clean_text(payload.get(key))
        if value:
            return value
    return ""


def _first_numeric(*values: object) -> float | None:
    for value in values:
        parsed = _parse_float(value)
        if parsed is not None:
            return parsed
    return None


def _parse_observed_at(
    *,
    row: dict[str, Any],
    query_time: dict[str, Any] | None,
) -> datetime:
    raw_tlong = _clean_text(row.get("tlong"))
    if raw_tlong.isdigit():
        return datetime.fromtimestamp(int(raw_tlong) / 1000, tz=UTC)

    trade_date = _clean_text(row.get("d")) or _clean_text(
        query_time.get("sysDate") if query_time else None
    )
    trade_time = _clean_text(row.get("t")) or _clean_text(
        query_time.get("sysTime") if query_time else None
    )
    if trade_date and trade_time:
        try:
            localized = datetime.strptime(
                f"{trade_date} {trade_time}",
                "%Y%m%d %H:%M:%S",
            ).replace(tzinfo=_TAIPEI)
            return localized.astimezone(UTC)
        except ValueError:
            pass

    return datetime.now(UTC)


def _direction_from_change(change: float) -> str:
    if change > 0:
        return "up"
    if change < 0:
        return "down"
    return "flat"


def _build_title(name: str, change_percent: float) -> str:
    if change_percent > 0:
        verb = "gains"
    elif change_percent < 0:
        verb = "falls"
    else:
        verb = "moves"
    return f"{name} {verb} {abs(change_percent):.2f}%"


def _build_stock_url(
    *,
    code: str,
    trade_date: str,
    observed_at: datetime,
) -> str:
    effective_trade_date = trade_date or observed_at.astimezone(_TAIPEI).strftime(
        "%Y%m%d"
    )
    return (
        "https://www.twse.com.tw/zh/page/trading/exchange/STOCK_DAY.html"
        f"?date={effective_trade_date}&stockNo={code}"
    )


def _build_content(
    *,
    code: str,
    last_price: float,
    previous_close: float,
    price_change: float,
    change_percent: float,
    volume: int | None,
    open_price: float | None,
    high_price: float | None,
    low_price: float | None,
) -> str:
    parts = [
        f"TWSE {code} last {last_price:.2f}",
        f"prev close {previous_close:.2f}",
        f"change {price_change:+.2f} ({change_percent:+.2f}%)",
    ]
    if volume is not None:
        parts.append(f"volume {volume:,}")
    if open_price is not None:
        parts.append(f"open {open_price:.2f}")
    if high_price is not None:
        parts.append(f"high {high_price:.2f}")
    if low_price is not None:
        parts.append(f"low {low_price:.2f}")
    return ", ".join(parts) + "."


def _compact_metadata(
    metadata: dict[str, object],
) -> dict[str, object]:
    return {
        key: value
        for key, value in metadata.items()
        if value not in {"", None}
    }
