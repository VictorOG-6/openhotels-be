import logging
import os
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

DEFAULT_DATA_URL = "https://api.liteapi.travel/v3.0"
DEFAULT_BOOK_URL = "https://book.liteapi.travel/v3.0"
_KEY = re.compile(r"^[A-Za-z0-9._-]{8,200}$")
_ALLOWED_HOSTS = {
    "api.liteapi.travel",
    "book.liteapi.travel",
}


@dataclass(frozen=True, slots=True)
class NuiteeSettings:
    private_key: str = field(repr=False)
    public_key: str = field(repr=False)
    data_base_url: str
    book_base_url: str
    timeout_seconds: float = 35.0
    configured: bool = False

    @property
    def sandbox(self) -> bool:
        return self.private_key.startswith("sand_")

    @classmethod
    def from_env(cls) -> "NuiteeSettings":
        _load_dotenv()
        private_key = os.getenv("NUITEE_PRIVATE_KEY", "").strip()
        public_key = os.getenv("NUITEE_PUBLIC_KEY", "").strip()
        data_raw = (
            os.getenv("NUITEE_API_URL") or os.getenv("NUITE_API_URL") or DEFAULT_DATA_URL
        )
        book_raw = os.getenv("NUITEE_BOOK_API_URL") or DEFAULT_BOOK_URL
        timeout = _timeout(os.getenv("NUITEE_TIMEOUT_SECONDS"))

        blank = cls(
            private_key="",
            public_key="",
            data_base_url=DEFAULT_DATA_URL,
            book_base_url=DEFAULT_BOOK_URL,
            timeout_seconds=timeout,
            configured=False,
        )
        if not private_key or not public_key:
            logger.error("Nuitee credentials are not configured")
            return blank
        if not _KEY.fullmatch(private_key) or not _KEY.fullmatch(public_key):
            logger.error("Nuitee credentials are not in the expected format")
            return blank
        try:
            data_base_url = normalize_base_url(data_raw, "api.liteapi.travel")
            book_base_url = normalize_base_url(book_raw, "book.liteapi.travel")
        except ValueError:
            logger.error("Nuitee API URL is invalid")
            return blank
        return cls(
            private_key=private_key,
            public_key=public_key,
            data_base_url=data_base_url,
            book_base_url=book_base_url,
            timeout_seconds=timeout,
            configured=True,
        )


def normalize_base_url(value: str, expected_host: str) -> str:
    if expected_host not in _ALLOWED_HOSTS:
        raise ValueError("Nuitee host is not allowed")
    parsed = urlsplit(value.strip())
    if (
        parsed.scheme != "https"
        or parsed.hostname != expected_host
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Nuitee URL must be https on the official host")
    path = parsed.path.rstrip("/")
    return f"https://{expected_host}{path}"


def _timeout(raw: str | None) -> float:
    if not raw:
        return 35.0
    try:
        value = float(raw)
    except ValueError:
        return 35.0
    if value < 5 or value > 60:
        return 35.0
    return value


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(override=False)
