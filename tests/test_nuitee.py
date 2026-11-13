import hashlib
import hmac
import json
import os
import unittest
from unittest.mock import patch

import httpx
from fastapi import HTTPException
from starlette.requests import Request

from schemas.nuitee import (
    BookRequest,
    BookingListQuery,
    HotelListQuery,
    HotelRatesRequest,
    PrebookRequest,
)
from services.nuitee.authz import resolve_caller, scoped_tag_filter
from services.nuitee.client import NuiteeClient, NuiteeError
from services.nuitee.settings import NuiteeSettings, normalize_base_url
from services.nuitee.signing import authorization_header, request_signature


PRIVATE = "sand_test_private_key"
PUBLIC = "public-key-123456"


def settings() -> NuiteeSettings:
    return NuiteeSettings(
        private_key=PRIVATE,
        public_key=PUBLIC,
        data_base_url="https://api.liteapi.travel/v3.0",
        book_base_url="https://book.liteapi.travel/v3.0",
        configured=True,
    )


class SigningTests(unittest.TestCase):
    def test_signature_matches_provider_digest(self):
        timestamp = "1610000000"
        signed = request_signature(PRIVATE, PUBLIC, timestamp)
        expected = hashlib.sha512(f"{PRIVATE}{PUBLIC}{timestamp}".encode()).hexdigest()
        self.assertEqual(signed, expected)

    def test_standard_hmac_is_not_the_provider_signature(self):
        timestamp = "1610000000"
        signed = request_signature(PRIVATE, PUBLIC, timestamp)
        standard = hmac.new(
            PRIVATE.encode(),
            f"{PUBLIC}{timestamp}".encode(),
            hashlib.sha512,
        ).hexdigest()
        self.assertNotEqual(signed, standard)

    def test_authorization_header_keeps_the_private_key_off_the_wire(self):
        timestamp, header = authorization_header(PRIVATE, PUBLIC, "1610000000")
        self.assertEqual(timestamp, "1610000000")
        self.assertIn(f"PublicKey={PUBLIC}", header)
        self.assertIn("Signature=", header)
        self.assertNotIn(PRIVATE, header)

    def test_rejects_header_injection_in_the_public_key(self):
        with self.assertRaises(ValueError):
            request_signature(PRIVATE, "public,Signature=forged", "1610000000")


class SettingsTests(unittest.TestCase):
    def test_rejects_non_nuitee_host(self):
        with self.assertRaises(ValueError):
            normalize_base_url("https://example.com/v3.0", "api.liteapi.travel")

    def test_rejects_plaintext_url(self):
        with self.assertRaises(ValueError):
            normalize_base_url("http://api.liteapi.travel/v3.0", "api.liteapi.travel")


class SchemaTests(unittest.TestCase):
    def test_booking_list_requires_a_date_range(self):
        with self.assertRaises(ValueError):
            BookingListQuery.model_validate({})

    def test_rates_require_hotel_ids(self):
        with self.assertRaises(ValueError):
            HotelRatesRequest.model_validate(
                {
                    "occupancies": [{"adults": 2}],
                    "currency": "usd",
                    "guestNationality": "us",
                    "checkin": "2027-01-15",
                    "checkout": "2027-01-16",
                }
            )

    def test_rates_reject_hotel_list_fields(self):
        with self.assertRaises(ValueError):
            HotelRatesRequest.model_validate(
                {
                    "hotelIds": ["lp1897"],
                    "occupancies": [{"adults": 2}],
                    "currency": "USD",
                    "guestNationality": "US",
                    "checkin": "2027-01-15",
                    "checkout": "2027-01-17",
                    "countryCode": "US",
                }
            )

    def test_rates_accepts_provider_payload(self):
        payload = HotelRatesRequest.model_validate(
            {
                "hotelIds": ["lp1897"],
                "occupancies": [
                    {"rooms": 1, "adults": 2, "children": [11]},
                    {"rooms": 1, "adults": 1},
                ],
                "currency": "USD",
                "guestNationality": "US",
                "checkin": "2027-01-15",
                "checkout": "2027-01-17",
                "timeout": 6,
                "roomMapping": True,
            }
        )
        dumped = payload.model_dump(by_alias=True, exclude_none=True)
        self.assertEqual(dumped["hotelIds"], ["lp1897"])
        self.assertNotIn("countryCode", dumped)

    def test_hotel_list_requires_an_anchor(self):
        with self.assertRaises(ValueError):
            HotelListQuery.model_validate({"cityName": "New York"})

    def test_hotel_list_country_code_is_enough(self):
        query = HotelListQuery.model_validate({"countryCode": "US", "cityName": "New York"})
        self.assertEqual(query.country_code, "US")

    def test_book_rejects_raw_card_payloads(self):
        with self.assertRaises(ValueError):
            BookRequest.model_validate(
                {
                    "prebookId": "prebook123",
                    "holder": {
                        "firstName": "Ada",
                        "lastName": "Lovelace",
                        "email": "ada@example.com",
                        "phone": "123456789",
                    },
                    "guests": [
                        {
                            "occupancyNumber": 1,
                            "firstName": "Ada",
                            "lastName": "Lovelace",
                            "email": "ada@example.com",
                        }
                    ],
                    "payment": {
                        "method": "CREDIT_CARD",
                        "billingInfo": {"creditCardNumber": "4242424242424242"},
                    },
                }
            )

    def test_prebook_serializes_provider_field_names(self):
        payload = PrebookRequest.model_validate(
            {"offerId": "offer_abc-123", "usePaymentSdk": False}
        )
        dumped = payload.model_dump(by_alias=True, exclude_none=True)
        self.assertEqual(dumped["offerId"], "offer_abc-123")
        self.assertEqual(dumped["usePaymentSdk"], False)

    def test_book_transaction_uses_provider_field_names(self):
        payload = BookRequest.model_validate(
            {
                "prebookId": "prebook123",
                "holder": {
                    "firstName": "Ada",
                    "lastName": "Lovelace",
                    "email": "ada@example.com",
                    "phone": "123456789",
                },
                "guests": [
                    {
                        "occupancyNumber": 1,
                        "firstName": "Ada",
                        "lastName": "Lovelace",
                        "email": "ada@example.com",
                    }
                ],
                "payment": {"method": "TRANSACTION_ID", "transactionId": "txn_123"},
            }
        )
        dumped = payload.model_dump(by_alias=True, exclude_none=True)
        self.assertEqual(dumped["payment"]["transactionId"], "txn_123")
        self.assertEqual(dumped["holder"]["firstName"], "Ada")


class AccessTests(unittest.TestCase):
    def test_production_without_local_auth_fails_closed(self):
        request = Request({"type": "http", "headers": []})
        with patch.dict(os.environ, {"SECRET_KEY": "", "ALGORITHM": ""}, clear=False):
            with self.assertRaises(HTTPException) as caught:
                resolve_caller(request, sandbox=False)
        self.assertEqual(caught.exception.status_code, 503)

    def test_user_tag_cannot_be_spoofed(self):
        scoped = scoped_tag_filter("USER_IDENTITY:keep,USER_ID:someone-else", "real-user")
        self.assertEqual(scoped, "USER_IDENTITY:keep,USER_ID:real-user")


class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_signs_get_and_does_not_send_the_private_key(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["auth"] = request.headers["Authorization"]
            seen["api_key"] = request.headers.get("X-API-Key")
            return httpx.Response(200, json={"data": []})

        client = NuiteeClient(settings(), transport=httpx.MockTransport(handler))
        try:
            body = await client.list_hotel_types()
        finally:
            await client.aclose()

        self.assertEqual(body, {"data": []})
        self.assertEqual(seen["url"], "https://api.liteapi.travel/v3.0/data/hotelTypes")
        self.assertIsNone(seen["api_key"])
        self.assertNotIn(PRIVATE, seen["auth"])
        self.assertTrue(seen["auth"].startswith(f"PublicKey={PUBLIC},Signature="))

    async def test_prebook_post_is_not_retried(self):
        calls = {"count": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["count"] += 1
            self.assertEqual(request.url.host, "book.liteapi.travel")
            self.assertEqual(request.url.path, "/v3.0/rates/prebook")
            body = json.loads(request.content)
            self.assertEqual(body["offerId"], "selected-offer")
            self.assertFalse(body["usePaymentSdk"])
            return httpx.Response(
                200,
                json={"data": {"prebookId": "pb_1", "offerId": "selected-offer"}},
            )

        client = NuiteeClient(settings(), transport=httpx.MockTransport(handler))
        try:
            body = await client.prebook_rate(
                {"offerId": "selected-offer", "usePaymentSdk": False}
            )
        finally:
            await client.aclose()

        self.assertEqual(calls["count"], 1)
        self.assertEqual(body["data"]["prebookId"], "pb_1")

    async def test_book_post_is_not_retried(self):
        calls = {"count": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["count"] += 1
            self.assertEqual(request.url.host, "book.liteapi.travel")
            self.assertEqual(request.url.path, "/v3.0/rates/book")
            return httpx.Response(503, json={"error": "unavailable"})

        client = NuiteeClient(settings(), transport=httpx.MockTransport(handler))
        try:
            with self.assertRaises(NuiteeError) as caught:
                await client.create_booking({"prebookId": "abc"})
        finally:
            await client.aclose()

        self.assertEqual(calls["count"], 1)
        self.assertEqual(caught.exception.status_code, 502)

    async def test_provider_auth_failure_does_not_echo_the_signature(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"error": "Invalid signature", "signature": "secret"})

        client = NuiteeClient(settings(), transport=httpx.MockTransport(handler))
        try:
            with self.assertRaises(NuiteeError) as caught:
                await client.list_hotel_types()
        finally:
            await client.aclose()

        self.assertEqual(caught.exception.status_code, 502)
        self.assertEqual(caught.exception.detail, "Hotel provider authentication failed")

    async def test_get_retries_a_transient_failure(self):
        calls = {"count": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["count"] += 1
            if calls["count"] == 1:
                return httpx.Response(503, json={"error": "try again"})
            return httpx.Response(200, json={"data": [{"id": 204}]})

        client = NuiteeClient(settings(), transport=httpx.MockTransport(handler))
        try:
            body = await client.list_hotel_types()
        finally:
            await client.aclose()

        self.assertEqual(calls["count"], 2)
        self.assertEqual(body["data"][0]["id"], 204)


if __name__ == "__main__":
    unittest.main()
