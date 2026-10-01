"""Serialize local admission while native requests reserve accelerator resources."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from .job_lifecycle import await_cleanup

admission_lock = asyncio.Lock()
_native_inflight = 0


class ResourceBusyError(Exception):
    """An incompatible video worker already owns local resources."""


def native_work_inflight() -> bool:
    return _native_inflight > 0


class NativeLease:
    def __init__(self) -> None:
        self._released = False

    async def _release(self) -> None:
        global _native_inflight
        async with admission_lock:
            if not self._released:
                self._released = True
                _native_inflight -= 1

    async def release(self) -> None:
        await await_cleanup(self._release())


async def reserve_native(video_busy: Callable[[], bool]) -> NativeLease:
    global _native_inflight
    async with admission_lock:
        if video_busy():
            raise ResourceBusyError("video_work_busy")
        _native_inflight += 1
        return NativeLease()


@asynccontextmanager
async def native_admission(video_busy: Callable[[], bool]) -> AsyncIterator[None]:
    lease = await reserve_native(video_busy)
    try:
        yield
    finally:
        await lease.release()
