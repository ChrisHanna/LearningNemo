"""Small JWT payload helpers used only after an access token is verified."""

from __future__ import annotations

import base64
import json
from typing import Any


def decode_jwt_payload(access_token: str) -> dict[str, Any]:
    """Decode claims without verifying them.

    Authorization code must call this only after a trusted verifier validates
    the exact same immutable token string.
    """
    try:
        payload_segment = access_token.split(".")[1]
        padding = "=" * (-len(payload_segment) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_segment + padding))
    except (IndexError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError("The access token payload is unreadable") from error
    if not isinstance(payload, dict):
        raise ValueError("The access token payload is invalid")
    return payload


def string_claim_values(claims: dict[str, Any], name: str) -> frozenset[str]:
    """Return a string or string-array claim as a normalized set."""
    value = claims.get(name)
    if value is None:
        raise ValueError(f"The access token is missing the required {name} claim")
    if isinstance(value, str):
        values = frozenset(value.split())
        if not values:
            raise ValueError(f"The access token has an empty {name} claim")
        return values
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        values = frozenset(value)
        if not values:
            raise ValueError(f"The access token has an empty {name} claim")
        return values
    raise ValueError(f"The access token has an invalid {name} claim")