"""One-time, action-bound capability tokens for process-level tool gates."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any, Callable


class CapabilityTokenError(ValueError):
    pass


class CapabilityTokenBroker:
    def __init__(
        self,
        secret: bytes | None = None,
        *,
        ttl_seconds: int = 300,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if ttl_seconds < 1:
            raise ValueError("ttl_seconds must be positive")
        self.secret = secret or secrets.token_bytes(32)
        self.ttl_seconds = ttl_seconds
        self.clock = clock
        self._consumed: set[str] = set()

    def issue(self, action: Any, risk: int) -> str:
        now = int(self.clock())
        payload = {
            "nonce": secrets.token_urlsafe(18),
            "issued": now,
            "expires": now + self.ttl_seconds,
            "action": self.action_digest(action),
            "max_risk": int(risk),
        }
        body = self._encode(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        )
        signature = self._encode(
            hmac.new(self.secret, body.encode(), hashlib.sha256).digest()
        )
        return f"{body}.{signature}"

    def consume(self, token: str, action: Any, risk: int) -> None:
        payload = self._decode_and_verify(token)
        nonce = str(payload["nonce"])
        if nonce in self._consumed:
            raise CapabilityTokenError("capability token has already been consumed")
        if int(self.clock()) > int(payload["expires"]):
            raise CapabilityTokenError("capability token expired")
        if payload["action"] != self.action_digest(action):
            raise CapabilityTokenError("capability token is bound to another action")
        if int(risk) > int(payload["max_risk"]):
            raise CapabilityTokenError("action risk exceeds token grant")
        self._consumed.add(nonce)

    def _decode_and_verify(self, token: str) -> dict[str, object]:
        try:
            body, supplied_signature = token.split(".", 1)
        except ValueError as exc:
            raise CapabilityTokenError("malformed capability token") from exc
        expected = self._encode(
            hmac.new(self.secret, body.encode(), hashlib.sha256).digest()
        )
        if not hmac.compare_digest(supplied_signature, expected):
            raise CapabilityTokenError("invalid capability token signature")
        try:
            return json.loads(self._decode(body))
        except (ValueError, json.JSONDecodeError) as exc:
            raise CapabilityTokenError("invalid capability token payload") from exc

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
