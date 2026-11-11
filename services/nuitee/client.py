import asyncio
import logging
import re
from typing import Any
from urllib.parse import urlsplit

import httpx

from services.nuitee.settings import NuiteeSettings
from services.nuitee.signing import authorization_header

logger = logging.getLogger(__name__)

_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_RETRYABLE = {429, 502, 503, 504}
_SENSITIVE_KEYS = {
    "apikey",
    "api_key",
    "authorization",
    "signature",
    "x-api-key",
    "creditcardnumber",
    "creditcardidentifier",
    "cvv",
    "cvc",
}


class NuiteeError(Exception):
    def __init__(self, status_code: int, detail: str | dict[str, Any]):
        self.status_code = status_code
        self.detail = detail
        super().__init__("Nuitee request failed")


class NuiteeClient:
    """Server-side Nuitee Connect client.

    Each call gets a new timestamp and signature. The private key is not
    placed in any header. Redirects and environment proxies are disabled
    so the signature cannot be forwarded to another host.
    """

    def __init__(
        self,
        settings: NuiteeSettings,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.settings = settings
        self._client = httpx.AsyncClient(
            transport=transport,
            timeout=httpx.Timeout(settings.timeout_seconds, connect=5.0),
            follow_redirects=False,
            trust_env=False,
            headers={
                "Accept": "application/json",
                "User-Agent": "openhotels-be",
            },
        )

    @classmethod
    def from_env(cls) -> "NuiteeClient":
        return cls(NuiteeSettings.from_env())

    async def aclose(self) -> None:
        await self._client.aclose()

    async def list_hotels(self, params: dict[str, Any]) -> Any:
        return await self._request("GET", self._data_url("/data/hotels"), params=params)

    async def get_hotel(self, hotel_id: str, params: dict[str, Any]) -> Any:
        _require_id(hotel_id)
        query = {"hotelId": hotel_id, **params}
        return await self._request("GET", self._data_url("/data/hotel"), params=query)

    async def list_hotel_types(self) -> Any:
        return await self._request("GET", self._data_url("/data/hotelTypes"))

    async def hotel_rates(self, body: dict[str, Any]) -> Any:
        timeout = _rates_timeout(body.get("timeout"), self.settings.timeout_seconds)
        return await self._request(
            "POST",
            self._data_url("/hotels/rates"),
            json_body=body,
            timeout=timeout,
        )

    async def prebook_rate(self, body: dict[str, Any]) -> Any:
        timeout = _rates_timeout(body.get("timeout"), self.settings.timeout_seconds)
        return await self._request(
            "POST",
            self._book_url("/rates/prebook"),
            json_body=body,
            timeout=timeout,
            idempotent=False,
        )

    async def create_booking(self, body: dict[str, Any]) -> Any:
        return await self._request(
            "POST",
            self._book_url("/rates/book"),
            json_body=body,
            idempotent=False,
        )

    async def list_bookings(self, params: dict[str, Any]) -> Any:
        return await self._request("GET", self._book_url("/bookings/"), params=params)

    async def get_booking(self, booking_id: str, params: dict[str, Any]) -> Any:
        _require_id(booking_id)
        return await self._request(
            "GET",
            self._book_url(f"/bookings/{booking_id}"),
            params=params,
        )

    async def _request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        timeout: httpx.Timeout | None = None,
        idempotent: bool = True,
    ) -> Any:
        if not self.settings.configured:
            raise NuiteeError(503, "Hotel provider is not configured")
        _require_allowed_url(url)

        attempts = 3 if idempotent else 1
        query = _clean_params(params)
        for attempt in range(attempts):
            _, auth = authorization_header(
                self.settings.private_key,
                self.settings.public_key,
            )
            headers = {
                "Authorization": auth,
                "Content-Type": "application/json",
            }
            try:
                response = await self._client.request(
                    method,
                    url,
                    params=query,
                    json=json_body,
                    headers=headers,
                    timeout=timeout,
                )
            except httpx.TimeoutException as exc:
                if attempt + 1 < attempts:
                    await asyncio.sleep(0.2 * (2**attempt))
                    continue
                raise NuiteeError(504, "Hotel provider timed out") from exc
            except httpx.HTTPError as exc:
                if attempt + 1 < attempts:
                    await asyncio.sleep(0.2 * (2**attempt))
                    continue
                raise NuiteeError(502, "Hotel provider is unreachable") from exc

            logger.info(
                "nuitee %s %s %s",
                method,
                urlsplit(url).path,
                response.status_code,
            )
            if response.status_code in _RETRYABLE and attempt + 1 < attempts:
                await asyncio.sleep(_retry_delay(response, attempt))
                continue
            return _parse(response)
        raise NuiteeError(502, "Hotel provider request failed")

    def _data_url(self, path: str) -> str:
        return _join(self.settings.data_base_url, path)

    def _book_url(self, path: str) -> str:
        return _join(self.settings.book_base_url, path)


def _join(base: str, path: str) -> str:
    return f"{base.rstrip('/')}/{path.lstrip('/')}"


def _require_id(value: str) -> None:
    if not _ID.fullmatch(value):
        raise NuiteeError(400, "Invalid identifier")


def _require_allowed_url(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in {
        "api.liteapi.travel",
        "book.liteapi.travel",
    }:
        raise NuiteeError(502, "Hotel provider URL is not allowed")


def _clean_params(params: dict[str, Any] | None) -> dict[str, str] | None:
    if not params:
        return None
    cleaned: dict[str, str] = {}
    for key, value in params.items():
        if value is None:
            continue
        if isinstance(value, bool):
            cleaned[key] = "true" if value else "false"
        else:
            cleaned[key] = str(value)
    return cleaned or None


def _rates_timeout(requested: Any, default: float) -> httpx.Timeout:
    read = default
    if isinstance(requested, int) and 1 <= requested <= 30:
        read = float(requested) + 5.0
    return httpx.Timeout(read, connect=5.0)


def _retry_delay(response: httpx.Response, attempt: int) -> float:
    retry_after = response.headers.get("Retry-After", "")
    if retry_after.isdigit():
        return min(float(retry_after), 2.0)
    return min(0.2 * (2**attempt), 2.0)


def _parse(response: httpx.Response) -> Any:
    if response.status_code == 204 or not response.content:
        if 200 <= response.status_code < 300:
            return None
    if 200 <= response.status_code < 300:
        try:
            return response.json()
        except ValueError as exc:
            raise NuiteeError(502, "Hotel provider returned an unreadable response") from exc
    if response.status_code in {401, 403}:
        logger.warning("Nuitee rejected the request signature")
        raise NuiteeError(502, "Hotel provider authentication failed")
    status = 429 if response.status_code == 429 else response.status_code
    if status >= 500:
        status = 502
    raise NuiteeError(status, _safe_detail(response))


def _safe_detail(response: httpx.Response) -> str | dict[str, Any]:
    try:
        body = response.json()
    except ValueError:
        return "Hotel provider request failed"
    if not isinstance(body, dict):
        return "Hotel provider request failed"
    return _redact(body)


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            if str(key).lower() in _SENSITIVE_KEYS:
                continue
            redacted[key] = _redact(item)
        return redacted
    if isinstance(value, list):
        return [_redact(item) for item in value[:50]]
    if isinstance(value, str):
        return value[:500]
    return value
