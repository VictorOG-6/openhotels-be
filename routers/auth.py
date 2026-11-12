import os
from datetime import UTC, datetime, timedelta
from typing import Annotated

from authlib.integrations.starlette_client import OAuth
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from fastapi.security import OAuth2PasswordRequestForm
from sqlmodel import select

from database import SessionDep
from models import (
    AuthModel,
    GoogleUser,
    Login,
    RefreshToken,
    RefreshTokenRequest,
    Token,
    User,
    password_hash,
)
from services.access_token import (
    create_access_token,
    create_refresh_token,
    verify_token,
)
from services.google import get_or_create_google_user

router = APIRouter(prefix="/auth", tags=["Auth"])
oauth = OAuth()

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI")
FRONTEND_URL = os.getenv("FRONTEND_URL")
REFRESH_TOKEN_EXPIRE_DAYS = os.getenv("REFRESH_TOKEN_EXPIRE_DAYS")

oauth.register(
    name="google",
    client_id=GOOGLE_CLIENT_ID,
    client_secret=GOOGLE_CLIENT_SECRET,
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)


def create_tokens_for_user(user: User, session: SessionDep):
    # Convert UUID to string before passing to token functions
    access_token = create_access_token(
        data={"sub": user.email, "user_id": str(user.id)}  # Add str() here
    )

    refresh_token = create_refresh_token(
        data={"sub": user.email, "user_id": str(user.id)}  # Add str() here
    )

    refresh_token_expires = timedelta(days=int(REFRESH_TOKEN_EXPIRE_DAYS))

    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
    )

    token_data = verify_token(refresh_token, "refresh", credentials_exception)

    db_refresh_token = RefreshToken(
        user_id=user.id,  # This is fine - SQLAlchemy handles UUID to DB
        token=token_data.jti,
        expires_at=datetime.now(UTC) + refresh_token_expires,
    )
    session.add(db_refresh_token)
    session.commit()

    return access_token, refresh_token


@router.get("/google")
async def login_google(request: Request):
    """Initiate Google OAuth flow"""
    return await oauth.google.authorize_redirect(request, GOOGLE_REDIRECT_URI)


@router.get("/callback/google")
async def auth_google(request: Request, session: SessionDep):
    """Handle Google OAuth callback and create user session"""
    try:
        user_response = await oauth.google.authorize_access_token(request)
        user_info = user_response.get("userinfo")

        if not user_info:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Failed to retrieve user information from Google",
            )

        google_user = GoogleUser(**user_info)
        user = get_or_create_google_user(google_user, session)
        access_token, refresh_token = create_tokens_for_user(user, session)

        # Important: Ensure everything is committed
        session.commit()

    except HTTPException:
        session.rollback()
        raise
    except Exception as e:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error during authentication: {str(e)}",
        )

    response = RedirectResponse(f"{FRONTEND_URL}/auth", status_code=302)

    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        secure=False,  # True in production
        samesite="lax",
        max_age=60 * 15,  # 15 minutes
    )

    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        secure=False,
        samesite="lax",
        max_age=60 * 60 * 24 * 7,  # 7 days
    )
    return response


@router.post("/login", response_model=AuthModel)
def login(request: Login, session: SessionDep):
    statement = select(User).where(User.email == request.username)
    user = session.exec(statement).first()

    if not user or not password_hash.verify(request.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect Credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.email_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Please verify your email before logging in",
        )

    access_token, refresh_token = create_tokens_for_user(user, session)

    return {
        "user": user,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
    }


@router.post("/token", response_model=Token)
def login_for_access_token(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()], session: SessionDep
):
    statement = select(User).where(User.email == form_data.username)
    user = session.exec(statement).first()

    if not user or not password_hash.verify(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token, refresh_token = create_tokens_for_user(user, session)

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
    }


@router.post("/refresh", response_model=Token)
def refresh_access_token(request: RefreshTokenRequest, session: SessionDep):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid refresh token",
        headers={"WWW-Authenticate": "Bearer"},
    )

    token_data = verify_token(request.refresh_token, "refresh", credentials_exception)

    if not token_data:
        raise credentials_exception

    # Check if token is in database and not revoked
    db_token = session.exec(
        select(RefreshToken).where(
            RefreshToken.token == token_data.jti, RefreshToken.revoked.is_(False)
        )
    ).first()

    if not db_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token has been revoked or does not exist",
        )

    # Check if token is expired
    if db_token.expires_at < datetime.now(UTC):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token has expired"
        )

    user = session.get(User, token_data.user_id)

    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    db_token.revoked = True
    session.commit()

    # Create new tokens
    access_token, new_refresh_token = create_tokens_for_user(user, session)

    return {
        "access_token": access_token,
        "refresh_token": new_refresh_token,
        "token_type": "bearer",
    }


@router.post("/logout")
def logout(request: Request, response: Response, session: SessionDep):
    # Get refresh token from HTTP-only cookie
    refresh_token = request.cookies.get("refresh_token")

    if not refresh_token:
        # Still delete cookies to be safe
        response.delete_cookie("access_token")
        response.delete_cookie("refresh_token")
        return {"message": "Successfully logged out"}

    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid refresh token",
    )

    # Verify refresh token
    token_data = verify_token(
        refresh_token,
        "refresh",
        credentials_exception,
    )

    # Revoke token in DB
    db_token = session.exec(
        select(RefreshToken).where(RefreshToken.token == token_data.jti)
    ).first()

    if db_token:
        db_token.revoked = True
        session.commit()

    # Delete cookies (VERY IMPORTANT)
    response.delete_cookie("access_token")
    response.delete_cookie("refresh_token")

    return {"message": "Successfully logged out"}