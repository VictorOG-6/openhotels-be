from datetime import UTC, datetime
from enum import Enum
from typing import Optional
from uuid import UUID, uuid4

from pwdlib import PasswordHash
from pydantic import EmailStr, model_validator
from sqlalchemy import Column, DateTime, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlmodel import Field, Relationship, SQLModel

password_hash = PasswordHash.recommended()


class GoogleUser(SQLModel):
    sub: str
    email: EmailStr
    name: str | None = None
    given_name: str | None = None
    family_name: str | None = None
    picture: str | None = None
    locale: str | None = None


class UserBase(SQLModel):
    name: str | None = Field(default=None)
    email: EmailStr = Field(unique=True, index=True)


class UserRole(str, Enum):
    USER = "user"
    ADMIN = "admin"
    STAFF = "staff"


class User(UserBase, table=True):
    id: UUID | None = Field(
        default_factory=uuid4,
        sa_column=Column(PG_UUID(as_uuid=True), primary_key=True, unique=True, nullable=False),
    )
    created_at: datetime = Field(
        default_factory=datetime.now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False, onupdate=func.now()),
    )
    hashed_password: str | None = None
    google_sub: str | None = Field(default=None, unique=True, index=True)
    profile_image_url: str | None = Field(default=None, max_length=500)
    hotels: list["Hotel"] = Relationship(back_populates="user")
    bookings: list["Booking"] = Relationship(back_populates="user")
    total_bookings: int = Field(default=0)
    total_spent: int = Field(default=0)
    role: UserRole = Field(default=UserRole.USER)


class UserRead(UserBase):
    id: UUID
    profile_image_url: str | None = None
    created_at: datetime
    updated_at: datetime
    total_bookings: int = 0
    total_spent: int = 0
    role: UserRole


class UserSummary(SQLModel):
    id: UUID
    name: str | None = None
    email: EmailStr


class UserCreate(UserBase):
    profile_image_url: str | None = Field(default=None)
    password: str


class UserUpdate(UserBase):
    name: str | None = Field(default=None, min_length=2)
    email: EmailStr | None = None
    profile_image_url: str | None = None
    password: str | None = Field(default=None, min_length=8, max_length=100)


class Login(SQLModel):
    username: str
    password: str


class Token(SQLModel):
    access_token: str
    token_type: str
    refresh_token: str


class TokenData(SQLModel):
    username: str | None = None
    user_id: str | None = None
    jti: str | None = None


class RefreshTokenRequest(SQLModel):
    refresh_token: str


class RefreshToken(SQLModel, table=True):
    id: UUID | None = Field(
        default_factory=uuid4,
        sa_column=Column(PG_UUID(as_uuid=True), primary_key=True, unique=True, nullable=False),
    )
    user_id: UUID = Field(foreign_key="user.id", nullable=False)
    token: str = Field(unique=True, index=True)
    expires_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    revoked: bool = Field(default=False)


class AuthModel(SQLModel):
    user: UserRead
    access_token: str
    refresh_token: str
    token_type: str


class BookingStatus(str, Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    REFUNDED = "refunded"
    FAILED = "failed"
    REFUND_PENDING = "refund_pending"
    REFUND_COMPLETED = "refund_completed"
    REFUND_FAILED = "refund_failed"


class Hotel(SQLModel, table=True):
    id: UUID | None = Field(
        default_factory=uuid4,
        sa_column=Column(PG_UUID(as_uuid=True), primary_key=True, unique=True, nullable=False),
    )
    user_id: UUID = Field(foreign_key="user.id", nullable=False, index=True)
    user: User = Relationship(back_populates="hotels")
    name: str
    description: str
    image_url: str
    location: str
    price: int
    rating: float
    num_reviews: int
    bookings: list["Booking"] = Relationship(back_populates="hotel")


class Booking(SQLModel, table=True):
    id: UUID | None = Field(
        default_factory=uuid4,
        sa_column=Column(PG_UUID(as_uuid=True), primary_key=True, unique=True, nullable=False),
    )
    user_id: UUID = Field(foreign_key="user.id", nullable=False)
    user: User = Relationship(back_populates="bookings")
    hotel_id: UUID = Field(foreign_key="hotel.id", nullable=False)
    hotel: Hotel = Relationship(back_populates="bookings")
    check_in_date: datetime
    check_out_date: datetime
    total_price: int
    status: BookingStatus = Field(default=BookingStatus.PENDING)