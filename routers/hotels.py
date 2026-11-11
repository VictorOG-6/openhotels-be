from typing import Annotated, Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response, status
from pydantic import ValidationError

from schemas.nuitee import (
    BookRequest,
    BookingDetailQuery,
    BookingListQuery,
    HotelDetailQuery,
    HotelListQuery,
    HotelRatesRequest,
    PrebookRequest,
)
from services.nuitee.authz import booking_owner, resolve_caller, scoped_tag_filter
from services.nuitee.client import NuiteeClient, NuiteeError

router = APIRouter(prefix="/hotels", tags=["Hotels"])

_ID = r"^[A-Za-z0-9_-]{1,64}$"


def get_nuitee(request: Request) -> NuiteeClient:
    client = getattr(request.app.state, "nuitee", None)
    if not isinstance(client, NuiteeClient) or not client.settings.configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Hotel provider is not configured",
        )
    return client


def get_caller(
    request: Request,
    client: NuiteeClient = Depends(get_nuitee),
):
    return resolve_caller(request, sandbox=client.settings.sandbox)


async def _provider(awaitable):
    try:
        return await awaitable
    except NuiteeError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


def _params(model) -> dict[str, Any]:
    return model.model_dump(by_alias=True, exclude_none=True, mode="json")


def _query(model_type, request: Request):
    try:
        return model_type.model_validate(dict(request.query_params))
    except ValidationError as exc:
        detail = [
            {
                "loc": ["query", *error.get("loc", ())],
                "msg": error.get("msg", "Invalid value"),
                "type": error.get("type", "value_error"),
            }
            for error in exc.errors()
        ]
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=detail,
        ) from exc


@router.get("/types")
async def list_hotel_types(client: NuiteeClient = Depends(get_nuitee)):
    result = await _provider(client.list_hotel_types())
    if result is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return result


@router.get("")
async def list_hotels(request: Request, client: NuiteeClient = Depends(get_nuitee)):
    query = _query(HotelListQuery, request)
    result = await _provider(client.list_hotels(_params(query)))
    if result is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return result


@router.post("/rates")
async def hotel_rates(body: HotelRatesRequest, client: NuiteeClient = Depends(get_nuitee)):
    result = await _provider(client.hotel_rates(_params(body)))
    if result is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return result


@router.post("/prebook")
async def prebook_rate(
    body: PrebookRequest,
    _caller=Depends(get_caller),
    client: NuiteeClient = Depends(get_nuitee),
):
    result = await _provider(client.prebook_rate(_params(body)))
    if result is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return result


@router.post("/bookings")
async def create_booking(
    body: BookRequest,
    caller=Depends(get_caller),
    client: NuiteeClient = Depends(get_nuitee),
):
    payload = _params(body)
    tags = dict(payload.get("customTags") or {})
    if caller.user_id:
        if "USER_ID" not in tags and len(tags) >= 5:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="customTags accepts at most 4 keys; USER_ID is added for the signed-in user",
            )
        tags["USER_ID"] = caller.user_id
        payload["customTags"] = tags
    payload.setdefault("clientReference", str(uuid4()))
    return await _provider(client.create_booking(payload))


@router.get("/bookings")
async def list_bookings(
    request: Request,
    caller=Depends(get_caller),
    client: NuiteeClient = Depends(get_nuitee),
):
    query = _query(BookingListQuery, request)
    params = _params(query)
    if caller.user_id:
        params["customTags"] = scoped_tag_filter(params.get("customTags"), caller.user_id)
    result = await _provider(client.list_bookings(params))
    if result is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return result


@router.get("/bookings/{booking_id}")
async def get_booking(
    request: Request,
    booking_id: Annotated[str, Path(pattern=_ID)],
    caller=Depends(get_caller),
    client: NuiteeClient = Depends(get_nuitee),
):
    query = _query(BookingDetailQuery, request)
    result = await _provider(client.get_booking(booking_id, _params(query)))
    if result is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    if caller.user_id and booking_owner(result) != caller.user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Booking not found")
    return result


@router.get("/{hotel_id}")
async def get_hotel(
    request: Request,
    hotel_id: Annotated[str, Path(pattern=_ID)],
    client: NuiteeClient = Depends(get_nuitee),
):
    query = _query(HotelDetailQuery, request)
    result = await _provider(client.get_hotel(hotel_id, _params(query)))
    if result is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return result
