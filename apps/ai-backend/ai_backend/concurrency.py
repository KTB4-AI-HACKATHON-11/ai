from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

Result = TypeVar("Result")


class AiBusyError(Exception):
    """AI 호출 슬롯과 대기열이 모두 찼을 때 발생한다."""

    def __init__(self, retry_after_seconds: int = 3) -> None:
        super().__init__("AI request capacity exhausted")
        self.retry_after_seconds = retry_after_seconds


class AiRequestLimiter:
    def __init__(
        self, max_concurrent: int, max_queued: int, queue_timeout_seconds: float = 3
    ) -> None:
        if max_concurrent < 1:
            raise ValueError("max_concurrent는 1 이상이어야 합니다.")
        if max_queued < 0:
            raise ValueError("max_queued는 0 이상이어야 합니다.")
        if queue_timeout_seconds <= 0:
            raise ValueError("queue_timeout_seconds는 0보다 커야 합니다.")
        self._max_concurrent = max_concurrent
        self._max_queued = max_queued
        self._queue_timeout_seconds = queue_timeout_seconds
        self._active = 0
        self._queued = 0
        self._condition = asyncio.Condition()

    async def run(self, factory: Callable[[], Awaitable[Result]]) -> Result:
        async with self._condition:
            if self._active >= self._max_concurrent:
                if self._queued >= self._max_queued:
                    raise AiBusyError
                self._queued += 1
                try:
                    try:
                        async with asyncio.timeout(self._queue_timeout_seconds):
                            await self._condition.wait_for(
                                lambda: self._active < self._max_concurrent
                            )
                    except TimeoutError as error:
                        raise AiBusyError(self.retry_after_seconds) from error
                finally:
                    self._queued -= 1
            self._active += 1

        try:
            return await factory()
        finally:
            async with self._condition:
                self._active -= 1
                self._condition.notify(1)

    @property
    def retry_after_seconds(self) -> int:
        return max(1, round(self._queue_timeout_seconds))
