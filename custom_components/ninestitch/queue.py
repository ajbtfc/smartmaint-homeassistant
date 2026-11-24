"""The offline queue: readings wait here until Ninestitch has them.

Persisted (a Home Assistant `Store`), so a restart or an internet outage loses nothing; capped at 24
hours or 10,000 readings, oldest dropped first. Each reading keeps the time it happened, so resending
one Ninestitch already has counts as a duplicate, never a second reading.

`flush()` sends what's waiting, a batch at a time, and decides when to try again:

  delivered              the next batch straight away
  can't connect / 5xx    back off: 30 s, 1 min, 2 min … up to 15 minutes
  429                    as long as Retry-After says
  402 (not on Plus)      keep everything, try again in 30 minutes
  401 (disconnected)     keep everything and stop; the integration asks to pair again
  400 / 413              that batch can never work: drop it, carry on
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any, Protocol

from .api import AuthFailed, CannotConnect, NinestitchError, PlanRequired, RateLimited
from .const import MAX_BATCH, QUEUE_MAX_AGE, QUEUE_MAX_READINGS

_LOGGER = logging.getLogger(__name__)

BACKOFF_START = 30.0
BACKOFF_MAX = 15 * 60.0
PLAN_RETRY = 30 * 60.0


class _Store(Protocol):
    async def async_load(self) -> Any: ...
    async def async_save(self, data: Any) -> None: ...


class _Sender(Protocol):
    async def send_readings(self, readings: list[dict[str, Any]]) -> dict[str, Any]: ...


class ReadingQueue:
    """Readings waiting for Ninestitch."""

    def __init__(
        self,
        api: _Sender,
        store: _Store,
        now: Callable[[], float] = time.time,
        max_readings: int = QUEUE_MAX_READINGS,
        max_age: float = QUEUE_MAX_AGE,
    ) -> None:
        self._api = api
        self._store = store
        self._now = now
        self._max = max_readings
        self._max_age = max_age
        self.items: list[dict[str, Any]] = []
        self.next_attempt: float = 0.0
        self._backoff = BACKOFF_START
        self.auth_failed = False
        self.plan_required = False
        self.dropped = 0
        self.last_result: dict[str, Any] | None = None
        self.last_error: str | None = None

    async def async_load(self) -> None:
        data = await self._store.async_load()
        if isinstance(data, dict) and isinstance(data.get("items"), list):
            self.items = [i for i in data["items"] if isinstance(i, dict) and "reading" in i]
        self._trim()

    async def async_save(self) -> None:
        await self._store.async_save({"items": self.items})

    def __len__(self) -> int:
        return len(self.items)

    def add(self, reading: dict[str, Any]) -> None:
        self.items.append({"queued_at": self._now(), "reading": reading})
        self._trim()

    def _trim(self) -> None:
        cutoff = self._now() - self._max_age
        before = len(self.items)
        self.items = [i for i in self.items if float(i.get("queued_at", 0)) >= cutoff]
        if len(self.items) > self._max:
            self.items = self.items[len(self.items) - self._max :]
        if len(self.items) < before:
            self.dropped += before - len(self.items)
            _LOGGER.warning(
                "Dropped %d reading(s) that waited more than 24 hours or beyond 10,000",
                before - len(self.items),
            )

    def due(self) -> bool:
        """Something to send, and it's time to try."""
        return bool(self.items) and not self.auth_failed and self._now() >= self.next_attempt

    def resume(self) -> None:
        """After pairing again: try at once."""
        self.auth_failed = False
        self.plan_required = False
        self.next_attempt = 0.0
        self._backoff = BACKOFF_START

    async def flush(self) -> None:
        """Send what's waiting until it's all delivered or a retry has been scheduled."""
        self._trim()
        changed = False
        while self.items and not self.auth_failed:
            batch = self.items[:MAX_BATCH]
            try:
                result = await self._api.send_readings([i["reading"] for i in batch])
            except RateLimited as err:
                self._retry_in(err.retry_after, err)
                break
            except CannotConnect as err:
                self._retry_in(self._backoff, err)
                self._backoff = min(self._backoff * 2, BACKOFF_MAX)
                break
            except AuthFailed as err:
                self.auth_failed = True
                self.last_error = err.code
                break
            except PlanRequired as err:
                self.plan_required = True
                self._retry_in(PLAN_RETRY, err)
                break
            except NinestitchError as err:
                _LOGGER.warning("Ninestitch refused %d reading(s): %s (%s)", len(batch), err.message, err.code)
                self.last_error = err.code
                del self.items[: len(batch)]
                changed = True
                continue
            del self.items[: len(batch)]
            changed = True
            self.last_result = result
            self.last_error = None
            self.plan_required = False
            self._backoff = BACKOFF_START
            self.next_attempt = 0.0
            for rejected in result.get("rejected") or []:
                _LOGGER.info("Ninestitch didn't record %s: %s", rejected.get("entity_id"), rejected.get("reason"))
        if changed or self.items:
            await self.async_save()

    def _retry_in(self, seconds: float, err: NinestitchError) -> None:
        self.next_attempt = self._now() + seconds
        self.last_error = err.code
        _LOGGER.debug("Readings wait %.0f s before the next try (%s)", seconds, err.code)
