import re
from datetime import date
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

_TAG_KEY = re.compile(r"^[A-Z0-9_-]+$")
_HOTEL_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_ID_LIST = re.compile(r"^[A-Za-z0-9_,-]{1,20000}$")


def to_camel(name: str) -> str:
    head, *tail = name.split("_")
    return head + "".join(part.capitalize() for part in tail)


class NuiteeModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
    )

    @model_validator(mode="before")
    @classmethod
    def blank_strings_are_missing(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        return {key: None if value == "" else value for key, value in data.items()}


class HotelListQuery(NuiteeModel):
    country_code: str | None = Field(default=None, min_length=2, max_length=2)
    city_name: str | None = Field(default=None, min_length=1, max_length=120)
    hotel_name: str | None = Field(default=None, min_length=1, max_length=200)
    offset: int | None = Field(default=None, ge=0)
    limit: int | None = Field(default=None, ge=1, le=5000)
    last_updated_at: str | None = Field(default=None, max_length=40)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    radius: int | None = Field(default=None, ge=1000, le=100000)
    ai_search: str | None = Field(default=None, min_length=1, max_length=500)
    timeout: float | None = Field(default=None, gt=0, le=30)
    zip: str | None = Field(default=None, min_length=1, max_length=20)
    min_rating: float | None = Field(default=None, ge=0, le=10)
    min_reviews_count: int | None = Field(default=None, ge=0)
    facility_ids: str | None = Field(default=None, max_length=500)
    hotel_type_ids: str | None = Field(default=None, max_length=500)
    chain_ids: str | None = Field(default=None, max_length=500)
    strict_facilities_filtering: bool | None = None
    star_rating: str | None = Field(default=None, max_length=80)
    place_id: str | None = Field(default=None, min_length=1, max_length=256)
    language: str | None = Field(default=None, min_length=2, max_length=8)
    hotel_ids: str | None = Field(default=None, max_length=20000)
    iata_code: str | None = Field(default=None, min_length=3, max_length=3)
    advanced_accessibility_only: bool | None = None

    @field_validator("country_code", "iata_code")
    @classmethod
    def uppercase_country(cls, value: str | None) -> str | None:
        return value.upper() if value else value

    @field_validator("hotel_ids")
    @classmethod
    def hotel_id_list(cls, value: str | None) -> str | None:
        if value and not _ID_LIST.fullmatch(value):
            raise ValueError("hotelIds must be a comma-separated list of ids")
        return value

    @model_validator(mode="after")
    def require_search(self) -> "HotelListQuery":
        has_lat = self.latitude is not None
        has_lng = self.longitude is not None
        if has_lat != has_lng:
            raise ValueError("latitude and longitude must be provided together")
        if self.radius is not None and not (has_lat and has_lng):
            raise ValueError("radius requires latitude and longitude")
        has_coordinates = has_lat and has_lng
        if not any(
            [
                self.country_code,
                has_coordinates,
                self.place_id,
                self.last_updated_at,
                self.iata_code,
                self.hotel_ids,
            ]
        ):
            raise ValueError(
                "Provide countryCode, latitude and longitude, placeId, lastUpdatedAt, "
                "iataCode, or hotelIds"
            )
        return self


class HotelDetailQuery(NuiteeModel):
    timeout: float | None = Field(default=None, gt=0, le=30)
    language: str | None = Field(default=None, min_length=2, max_length=8)
    advanced_accessibility_only: bool | None = None


class Occupancy(NuiteeModel):
    adults: int = Field(ge=1, le=10)
    children: list[Annotated[int, Field(ge=0, le=17)]] | None = Field(default=None, max_length=8)
    rooms: int | None = Field(default=None, ge=1, le=8)


class HotelRatesRequest(NuiteeModel):
    hotel_ids: list[str] = Field(min_length=1, max_length=5000)
    occupancies: list[Occupancy] = Field(min_length=1, max_length=8)
    currency: str = Field(min_length=3, max_length=3)
    guest_nationality: str = Field(min_length=2, max_length=2)
    checkin: date
    checkout: date
    timeout: int | None = Field(default=None, ge=1, le=30)
    room_mapping: bool | None = None

    @field_validator("currency", "guest_nationality")
    @classmethod
    def uppercase_codes(cls, value: str) -> str:
        return value.upper()

    @field_validator("hotel_ids")
    @classmethod
    def hotel_id_values(cls, value: list[str]) -> list[str]:
        if any(not _HOTEL_ID.fullmatch(hotel_id) for hotel_id in value):
            raise ValueError("hotelIds contain an invalid id")
        return value

    @model_validator(mode="after")
    def validate_stay_dates(self) -> "HotelRatesRequest":
        if self.checkout <= self.checkin:
            raise ValueError("checkout must be after checkin")
        return self


class Holder(NuiteeModel):
    first_name: str = Field(min_length=1, max_length=80)
    last_name: str = Field(min_length=1, max_length=80)
    email: EmailStr
    phone: str = Field(min_length=5, max_length=32)


class Guest(NuiteeModel):
    occupancy_number: int = Field(ge=1, le=20)
    first_name: str = Field(min_length=1, max_length=80)
    last_name: str = Field(min_length=1, max_length=80)
    email: EmailStr
    phone: str | None = Field(default=None, min_length=5, max_length=32)
    remarks: str | None = Field(default=None, max_length=500)


class AccountPayment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    method: Literal["ACC_CREDIT_CARD", "WALLET", "CREDIT"]


class TransactionPayment(NuiteeModel):
    method: Literal["TRANSACTION_ID"]
    transaction_id: str = Field(min_length=1, max_length=128)


Payment = Annotated[AccountPayment | TransactionPayment, Field(discriminator="method")]


class GuestAddress(BaseModel):
    model_config = ConfigDict(extra="forbid")
    city: str | None = Field(default=None, max_length=120)
    address: str | None = Field(default=None, max_length=200)
    country: str | None = Field(default=None, min_length=2, max_length=2)
    postal_code: str | None = Field(default=None, max_length=20)


class GuestPayment(BaseModel):
    """Nuitee expects snake_case keys on this object."""

    model_config = ConfigDict(extra="forbid")
    method: str = Field(min_length=1, max_length=40)
    phone: str = Field(min_length=5, max_length=32)
    payee_first_name: str = Field(min_length=1, max_length=80)
    payee_last_name: str = Field(min_length=1, max_length=80)
    last_4_digits: str = Field(pattern=r"^[0-9]{4}$")
    address: GuestAddress | None = None


class BookingMetadata(BaseModel):
    """Nuitee expects snake_case keys on this object."""

    model_config = ConfigDict(extra="forbid")
    ip: str | None = Field(default=None, max_length=64)
    country: str | None = Field(default=None, max_length=2)
    language: str | None = Field(default=None, max_length=16)
    platform: str | None = Field(default=None, max_length=64)
    device_id: str | None = Field(default=None, max_length=128)
    user_agent: str | None = Field(default=None, max_length=256)
    utm_medium: str | None = Field(default=None, max_length=80)
    utm_source: str | None = Field(default=None, max_length=80)
    utm_campaign: str | None = Field(default=None, max_length=80)


class PrebookRequest(NuiteeModel):
    offer_id: str = Field(min_length=1, max_length=256)
    use_payment_sdk: bool = False
    voucher_code: str | None = Field(default=None, min_length=1, max_length=64)
    include_credit_balance: bool | None = None


class BookRequest(NuiteeModel):
    prebook_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    client_reference: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )
    holder: Holder
    guests: list[Guest] = Field(min_length=1, max_length=20)
    payment: Payment
    metadata: BookingMetadata | None = None
    guest_payment: GuestPayment | None = None
    custom_tags: dict[str, str] | None = None

    @field_validator("custom_tags")
    @classmethod
    def tags_are_bounded(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        return _validate_tags(value, limit=5)


class BookingListQuery(NuiteeModel):
    start_date: date | None = None
    end_date: date | None = None
    booking_start_date: date | None = None
    booking_end_date: date | None = None
    status: Literal["confirmed", "cancelled"] | None = None
    payment_status: Literal["succeeded", "requires_capture"] | None = None
    sandbox: Literal[0, 1] | None = None
    custom_tags: str | None = Field(default=None, max_length=1500)
    timeout: float | None = Field(default=None, gt=0, le=30)

    @model_validator(mode="after")
    def date_ranges(self) -> "BookingListQuery":
        if bool(self.start_date) != bool(self.end_date):
            raise ValueError("startDate and endDate must be provided together")
        if bool(self.booking_start_date) != bool(self.booking_end_date):
            raise ValueError("bookingStartDate and bookingEndDate must be provided together")
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("endDate must be on or after startDate")
        if (
            self.booking_start_date
            and self.booking_end_date
            and self.booking_end_date < self.booking_start_date
        ):
            raise ValueError("bookingEndDate must be on or after bookingStartDate")
        if not (self.start_date and self.end_date) and not (
            self.booking_start_date and self.booking_end_date
        ):
            raise ValueError(
                "Provide a stay range (startDate and endDate) or a booking range "
                "(bookingStartDate and bookingEndDate)"
            )
        if self.custom_tags:
            _validate_tag_query(self.custom_tags)
        return self


class BookingDetailQuery(NuiteeModel):
    timeout: float | None = Field(default=None, gt=0, le=30)


def _validate_tags(value: dict[str, str] | None, limit: int) -> dict[str, str] | None:
    if value is None:
        return None
    if len(value) > limit:
        raise ValueError(f"customTags accepts at most {limit} keys")
    for key, item in value.items():
        if not _TAG_KEY.fullmatch(key):
            raise ValueError("customTags keys must match ^[A-Z0-9_-]+$")
        if not isinstance(item, str) or not item or len(item) > 255:
            raise ValueError("customTags values must be 1 to 255 characters")
        if any(char in item for char in (",", ":", "\n", "\r")):
            raise ValueError("customTags values cannot contain commas or colons")
    return value


def _validate_tag_query(value: str) -> None:
    pairs = [pair for pair in value.split(",") if pair]
    if len(pairs) > 5:
        raise ValueError("customTags accepts at most 5 pairs")
    for pair in pairs:
        if pair.count(":") != 1:
            raise ValueError("customTags filters must be KEY:VALUE pairs")
        key, item = pair.split(":", 1)
        if not _TAG_KEY.fullmatch(key) or not item or len(item) > 255:
            raise ValueError("customTags filter is invalid")
