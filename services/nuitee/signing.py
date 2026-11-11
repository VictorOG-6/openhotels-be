import hashlib
import re
import time

_TOKEN = re.compile(r"^[A-Za-z0-9._-]{8,200}$")
_TIMESTAMP = re.compile(r"^[0-9]{10,12}$")


class NuiteeSigningError(ValueError):
    """Signing material is missing or would break the Authorization header."""


def request_signature(private_key: str, public_key: str, timestamp: str) -> str:
    """Sign one Nuitee request.

    Nuitee documents this as HMAC-SHA512. The live API accepts
    SHA-512(private_key + public_key + unix_timestamp) as lowercase hex
    and rejects a standard HMAC-SHA512 of that material with 401.
    The private key is an input to the digest and is never sent.
    """
    if not _TOKEN.fullmatch(private_key) or not _TOKEN.fullmatch(public_key):
        raise NuiteeSigningError("Nuitee API keys are missing or malformed")
    if not _TIMESTAMP.fullmatch(timestamp):
        raise NuiteeSigningError("Nuitee signature timestamp is invalid")

    message = f"{private_key}{public_key}{timestamp}".encode()
    return hashlib.sha512(message).hexdigest()


def authorization_header(
    private_key: str,
    public_key: str,
    timestamp: str | None = None,
) -> tuple[str, str]:
    """Return (timestamp, Authorization header) for a single outbound call."""
    issued_at = timestamp if timestamp is not None else str(int(time.time()))
    signature = request_signature(private_key, public_key, issued_at)
    header = f"PublicKey={public_key},Signature={signature},Timestamp={issued_at}"
    return issued_at, header
