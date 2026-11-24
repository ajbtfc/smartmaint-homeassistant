"""Talking to Ninestitch: pairing, readings, the entity catalogue, the feed and actions.

Every endpoint answers errors the same way: an HTTP status and `details`, a JSON string whose `code`
says what happened (`token_invalid`, `plan_requires_live_sensors`, `rate_limited`, …).
"""

from __future__ import annotations

import json
from typing import Any

import aiohttp
from multidict import CIMultiDict

TIMEOUT = aiohttp.ClientTimeout(total=30)


class NinestitchError(Exception):
    """Ninestitch answered with an error."""

    def __init__(self, status: int, code: str, message: str, params: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.params = params or {}


class CannotConnect(NinestitchError):
    """Ninestitch couldn't be reached, or had a problem of its own (5xx). Worth retrying."""


class AuthFailed(NinestitchError):
    """The connection token is unknown or was disconnected in Ninestitch (401)."""


class PlanRequired(NinestitchError):
    """The household isn't on Ninestitch Plus (402)."""


class RateLimited(NinestitchError):
    """Too many requests (429); `retry_after` says how long to wait, in seconds."""

    @property
    def retry_after(self) -> float:
        value = self.params.get("retry_after")
        return float(value) if isinstance(value, (int, float)) and value > 0 else 60.0


class NinestitchApi:
    """A connection to one Ninestitch household, by its token."""

    def __init__(self, session: aiohttp.ClientSession, url: str, token: str | None = None) -> None:
        self._session = session
        self._base = f"{url.rstrip('/')}/functions/v1"
        self.token = token

    async def _request(
        self,
        method: str,
        path: str,
        body: Any = None,
        headers: dict[str, str] | None = None,
        auth: bool = True,
    ) -> tuple[int, Any, CIMultiDict[str]]:
        hdrs = dict(headers or {})
        if auth:
            hdrs["Authorization"] = f"Bearer {self.token}"
        try:
            async with self._session.request(
                method,
                f"{self._base}/{path}",
                json=body,
                headers=hdrs,
                timeout=TIMEOUT,
            ) as res:
                text = await res.text()
                resp_headers = CIMultiDict(res.headers)  # header names aren't case-sensitive
                status = res.status
        except (TimeoutError, aiohttp.ClientError) as err:
            raise CannotConnect(0, "cannot_connect", f"Couldn't reach Ninestitch: {err}") from err

        if status == 304:
            return status, None, resp_headers
        try:
            data = json.loads(text) if text else None
        except ValueError:
            data = None
        if 200 <= status < 300:
            return status, data, resp_headers

        code, message, params = "failed", f"Ninestitch answered {status}", {}
        if isinstance(data, dict):
            message = str(data.get("message") or message)
            try:
                details = json.loads(data.get("details") or "{}")
                if isinstance(details, dict):
                    code = str(details.pop("code", code))
                    params = details
            except (TypeError, ValueError):
                pass
        if status == 429 and "retry_after" not in params:
            try:
                params["retry_after"] = float(resp_headers.get("Retry-After", "60"))
            except ValueError:
                params["retry_after"] = 60.0
        cls: type[NinestitchError] = NinestitchError
        if status == 401:
            cls = AuthFailed
        elif status == 402:
            cls = PlanRequired
        elif status == 429 and code != "pairing_rate_limited":
            cls = RateLimited
        elif status >= 500:
            cls = CannotConnect
        raise cls(status, code, message, params)

    async def pair(self, code: str, name: str) -> dict[str, Any]:
        """Swap a pairing code for a token: {token, household, device_id}."""
        _, data, _ = await self._request("POST", "ha-pair", {"code": code, "name": name}, auth=False)
        return data

    async def upload_catalogue(self, entities: list[dict[str, Any]]) -> int:
        _, data, _ = await self._request("POST", "ha-catalogue", {"entities": entities})
        return int((data or {}).get("stored", 0))

    async def send_readings(self, readings: list[dict[str, Any]]) -> dict[str, Any]:
        """{accepted, ignored, duplicates, rejected: [{entity_id, reason}]}."""
        _, data, _ = await self._request("POST", "ha-readings", {"readings": readings})
        return data or {}

    async def feed(self, etag: str | None = None) -> tuple[dict[str, Any] | None, str | None]:
        """The feed, and its ETag. None when it hasn't changed since `etag` (304)."""
        status, data, headers = await self._request("GET", "ha-feed", headers={"If-None-Match": etag} if etag else None)
        new_etag = headers.get("ETag") or etag
        return (None if status == 304 else data), new_etag

    async def ack(self, ids: list[int]) -> int:
        _, data, _ = await self._request("POST", "ha-ack", {"ids": ids})
        return int((data or {}).get("acknowledged", 0))

    async def action(self, work_order_id: str, action: str, person: str | None, idempotency_key: str) -> dict[str, Any]:
        """{ok, status} or {ok: false, needs_input: true, url, fields}."""
        _, data, _ = await self._request(
            "POST",
            "ha-action",
            {
                "work_order_id": work_order_id,
                "action": action,
                "person": person,
                "idempotency_key": idempotency_key,
            },
        )
        return data or {}
