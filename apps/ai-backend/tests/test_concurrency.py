from __future__ import annotations

import asyncio

import pytest

from ai_backend.concurrency import AiBusyError, AiRequestLimiter


def test_limiter_queues_up_to_limit_and_rejects_overflow() -> None:
    async def scenario() -> tuple[str, str]:
        limiter = AiRequestLimiter(max_concurrent=1, max_queued=1)
        started = asyncio.Event()
        release = asyncio.Event()

        async def first_call() -> str:
            started.set()
            await release.wait()
            return "first"

        async def second_call() -> str:
            return "second"

        first = asyncio.create_task(limiter.run(first_call))
        await started.wait()
        second = asyncio.create_task(limiter.run(second_call))
        await asyncio.sleep(0)

        with pytest.raises(AiBusyError):
            await limiter.run(second_call)

        release.set()
        return await first, await second

    assert asyncio.run(scenario()) == ("first", "second")


def test_limiter_releases_slot_after_failure() -> None:
    async def scenario() -> str:
        limiter = AiRequestLimiter(max_concurrent=1, max_queued=0)

        async def fail() -> str:
            raise RuntimeError("provider failed")

        async def succeed() -> str:
            return "ok"

        with pytest.raises(RuntimeError):
            await limiter.run(fail)
        return await limiter.run(succeed)

    assert asyncio.run(scenario()) == "ok"


def test_limiter_rejects_request_when_queue_wait_times_out() -> None:
    async def scenario() -> None:
        limiter = AiRequestLimiter(
            max_concurrent=1,
            max_queued=1,
            queue_timeout_seconds=0.02,
        )
        started = asyncio.Event()
        release = asyncio.Event()

        async def blocked() -> None:
            started.set()
            await release.wait()

        first = asyncio.create_task(limiter.run(blocked))
        await started.wait()
        try:
            with pytest.raises(AiBusyError):
                await limiter.run(blocked)
        finally:
            release.set()
            await first

    asyncio.run(scenario())
