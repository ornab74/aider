"""One-time, action-bound capability tokens for gated tool execution."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any


class CapabilityTokenError(RuntimeError):
    pass


@dataclass(frozen=True)
class CapabilityGrant:
    action_digest: str
    expires_at: int
    nonce: str


class CapabilityTokenIssuer:
    def __init__(self, secret: bytes | None = None) -> None:
        self.secret = secret or secrets.token_bytes(32)
        self.used_nonces: set[str] = set()

    def issue(self, action: Any, *, ttl_seconds: int = 120) -> str:
        if ttl_seconds < 1:
            raise ValueError("ttl_seconds must be positive")
        payload = {
            "action_digest": self.action_digest(action),
            "expires_at": int(time.time()) + ttl_seconds,
            "nonce": secrets.token_urlsafe(18),
        }
        encoded = self._encode(json.dumps(payload, sort_keys=True).encode())
        signature = self._encode(hmac.new(self.secret, encoded.encode(), hashlib.sha256).digest())
        return f"{encoded}.{signature}"

    def consume(self, token: str, action: Any) -> CapabilityGrant:
        try:
            encoded, signature = token.split(".", 1)
        except ValueError as exc:
            raise CapabilityTokenError("malformed capability token") from exc
        expected = self._encode(hmac.new(self.secret, encoded.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            raise CapabilityTokenError("invalid capability token signature")
        try:
            payload = json.loads(self._decode(encoded))
            grant = CapabilityGrant(**payload)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise CapabilityTokenError("invalid capability token payload") from exc
        if grant.expires_at < int(time.time()):
            raise CapabilityTokenError("capability token expired")
        if grant.nonce in self.used_nonces:
            raise CapabilityTokenError("capability token already consumed")
        if grant.action_digest != self.action_digest(action):
            raise CapabilityTokenError("capability token does not match this action")
        self.used_nonces.add(grant.nonce)
        return grant

    @staticmethod
    def action_digest(action: Any) -> str:
        payload = {
            "tool": str(action.tool),
            "body": str(action.body),
            "attributes": sorted(
                (str(key), str(value)) for key, value in action.attributes.items()
            ),
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _encode(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).rstrip(b"=").decode()

    @staticmethod
    def _decode(value: str) -> bytes:
        padding = "=" * (-len(value) % 4)
        return base64.urlsafe_b64decode(value + padding)
