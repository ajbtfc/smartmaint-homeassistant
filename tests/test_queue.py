"""The offline queue (#34): persisted, capped, retried with backoff, never loses or doubles readings."""

from __future__ import annotations

from typing import Any

from custom_components.ninestitch.api import AuthFailed, CannotConnect, NinestitchError, PlanRequired, RateLimited
from custom_components.ninestitch.queue import BACKOFF_MAX, PLAN_RETRY, ReadingQueue


class Clock:
    def __init__(self) -> None:
        self.t = 1_000_000.0

    def __call__(self) -> float:
        return self.t


class MemoryStore:
    def __init__(self, data: Any = None) -> None:
        self.data = data

    async def async_load(self) -> Any:
        return self.data

    async def async_save(self, data: Any) -> None:
        import copy

        self.data = copy.deepcopy(data)


class FakeApi:
    def __init__(self, *answers: Any) -> None:
        self.answers = list(answers)
        self.calls: list[list[dict[str, Any]]] = []

    async def send_readings(self, readings: list[dict[str, Any]]) -> dict[str, Any]:
        self.calls.append(readings)
        answer = self.answers.pop(0) if self.answers else {"accepted": len(readings), "rejected": []}
        if isinstance(answer, Exception):
            raise answer
        return answer


def reading(i: int) -> dict[str, Any]:
    return {
        "entity_id": "sensor.outback_odometer",
        "state": str(60000 + i),
        "at": f"2026-11-24T10:{i % 60:02d}:00+00:00",
    }


async def test_delivers_in_batches_of_500_and_empties_the_saved_queue() -> None:
    api, store = FakeApi(), MemoryStore()
    q = ReadingQueue(api, store, now=Clock())
    for i in range(1200):
        q.add(reading(i))
    await q.flush()
    assert [len(c) for c in api.calls] == [500, 500, 200]
    assert len(q) == 0
    assert store.data == {"items": []}


async def test_survives_a_restart() -> None:
    clock, store = Clock(), MemoryStore()
    q = ReadingQueue(FakeApi(CannotConnect(0, "cannot_connect", "offline")), store, now=clock)
    q.add(reading(1))
    q.add(reading(2))
    await q.flush()
    assert len(store.data["items"]) == 2

    again = ReadingQueue(FakeApi(), store, now=clock)
    await again.async_load()
    assert [i["reading"]["state"] for i in again.items] == ["60001", "60002"]


async def test_offline_backs_off_and_resends_the_same_readings() -> None:
    clock = Clock()
    api = FakeApi(CannotConnect(0, "cannot_connect", "x"), CannotConnect(503, "failed", "x"))
    q = ReadingQueue(api, MemoryStore(), now=clock)
    q.add(reading(1))
    await q.flush()
    assert q.next_attempt == clock.t + 30 and not q.due()
    clock.t += 30
    assert q.due()
    await q.flush()
    assert q.next_attempt == clock.t + 60, "doubles"
    clock.t += 60
    await q.flush()
    assert len(q) == 0
    assert api.calls[0] == api.calls[2], "the same reading, with the same time: a duplicate, not a second reading"

    slow = ReadingQueue(FakeApi(*[CannotConnect(0, "c", "x")] * 20), MemoryStore(), now=clock)
    slow.add(reading(1))
    for _ in range(20):
        clock.t = slow.next_attempt
        await slow.flush()
    assert slow.next_attempt - clock.t == BACKOFF_MAX


async def test_rate_limited_waits_for_retry_after() -> None:
    clock = Clock()
    q = ReadingQueue(FakeApi(RateLimited(429, "rate_limited", "slow", {"retry_after": 17})), MemoryStore(), now=clock)
    q.add(reading(1))
    await q.flush()
    assert q.next_attempt == clock.t + 17
    assert len(q) == 1


async def test_not_on_plus_keeps_readings_and_tries_later() -> None:
    clock = Clock()
    q = ReadingQueue(FakeApi(PlanRequired(402, "plan_requires_live_sensors", "Plus")), MemoryStore(), now=clock)
    q.add(reading(1))
    await q.flush()
    assert q.plan_required and len(q) == 1
    assert q.next_attempt == clock.t + PLAN_RETRY


async def test_disconnected_keeps_readings_and_stops_until_paired_again() -> None:
    clock = Clock()
    api = FakeApi(AuthFailed(401, "token_invalid", "gone"))
    q = ReadingQueue(api, MemoryStore(), now=clock)
    q.add(reading(1))
    await q.flush()
    assert q.auth_failed and len(q) == 1 and not q.due()
    q.resume()
    await q.flush()
    assert len(q) == 0 and len(api.calls) == 2


async def test_a_batch_ninestitch_can_never_accept_is_dropped_not_retried_forever() -> None:
    api = FakeApi(NinestitchError(400, "readings_invalid", "bad"))
    q = ReadingQueue(api, MemoryStore(), now=Clock())
    for i in range(3):
        q.add(reading(i))
    await q.flush()
    assert len(q) == 0 and q.last_error == "readings_invalid"


async def test_capped_at_10000_readings_and_24_hours_oldest_first() -> None:
    clock = Clock()
    q = ReadingQueue(FakeApi(), MemoryStore(), now=clock, max_readings=10_000)
    for i in range(10_005):
        q.add(reading(i))
    assert len(q) == 10_000
    assert q.items[0]["reading"]["state"] == str(60005), "the oldest went first"
    assert q.dropped == 5

    old = ReadingQueue(FakeApi(), MemoryStore(), now=clock)
    old.add(reading(1))
    clock.t += 24 * 3600 + 1
    old.add(reading(2))
    assert [i["reading"]["state"] for i in old.items] == ["60002"]
