import os
from dataclasses import dataclass

from fastapi import HTTPException, Request, status

_UNSAFE_ALGORITHMS = {"none", "null"}


@dataclass(frozen=True, slots=True)
class Caller:
    user_id: str | None


def resolve_caller(request: Request, *, sandbox: bool) -> Caller:
    """Identify the caller from the app access token.

    Sandbox keys can be used before local auth is configured. Production
    keys fail closed until SECRET_KEY and ALGORITHM are set, and every
    booking call must carry a bearer access token.
    """
    secret = os.getenv("SECRET_KEY", "").strip()
    algorithm = os.getenv("ALGORITHM", "").strip()
    if not secret or not algorithm or algorithm.lower() in _UNSAFE_ALGORITHMS:
        if sandbox:
            return Caller(user_id=None)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="User authentication is not configured",
        )

    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = header.split(" ", 1)[1].strip()
    payload = _decode_access_token(token, secret, algorithm)
    user_id = payload.get("user_id")
    subject = payload.get("sub")
    if not isinstance(user_id, str) or not isinstance(subject, str):
        raise _invalid_token()
    if not user_id or not subject or any(char in user_id for char in (",", ":", "\n", "\r")):
        raise _invalid_token()
    return Caller(user_id=user_id)


def booking_owner(payload: object) -> str | None:
    if not isinstance(payload, dict):
        return None
    node = payload.get("data", payload)
    if not isinstance(node, dict):
        return None
    tags = node.get("customTags")
    if not isinstance(tags, dict):
        return None
    owner = tags.get("USER_ID")
    if owner is None:
        return None
    return str(owner)


def scoped_tag_filter(existing: str | None, user_id: str) -> str:
    pairs = []
    if existing:
        for pair in existing.split(","):
            if not pair or pair.split(":", 1)[0] == "USER_ID":
                continue
            pairs.append(pair)
    pairs.append(f"USER_ID:{user_id}")
    if len(pairs) > 5:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="customTags accepts at most 4 filters besides the user tag",
        )
    return ",".join(pairs)


def _decode_access_token(token: str, secret: str, algorithm: str) -> dict:
    try:
        import jwt
        from jwt.exceptions import InvalidTokenError
    except ImportError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="User authentication is not configured",
        ) from exc
    try:
        payload = jwt.decode(token, secret, algorithms=[algorithm])
    except InvalidTokenError as exc:
        raise _invalid_token() from exc
    if payload.get("type") != "access":
        raise _invalid_token()
    return payload


def _invalid_token() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
