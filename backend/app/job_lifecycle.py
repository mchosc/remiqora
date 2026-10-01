"""Own job tasks and subprocess sessions through cancellation and shutdown."""
from __future__ import annotations

import asyncio
import os
import signal
import sys
from collections.abc import Awaitable, Mapping
from typing import IO, TypeVar

T = TypeVar("T")


async def await_cleanup(operation: Awaitable[T]) -> T:
    """Finish owned cleanup before propagating cancellation of its caller."""
    drained = asyncio.ensure_future(operation)
    cancelled = False
    while not drained.done():
        try:
            await asyncio.shield(drained)
        except asyncio.CancelledError:
            cancelled = True
    result = drained.result()
    if cancelled:
        raise asyncio.CancelledError
    return result


def request_cancel(task: asyncio.Task[None] | None) -> None:
    """Request cancellation synchronously before yielding to any queued jobs."""
    if task is None or task is asyncio.current_task():
        return
    if not task.done() and not task.cancelling():
        task.cancel()


async def cancel_and_wait(task: asyncio.Task[None] | None) -> None:
    """Cancel once, then await cleanup; repeated cancellation must not interrupt it."""
    if task is None or task is asyncio.current_task():
        return
    request_cancel(task)
    await await_cleanup(asyncio.gather(task, return_exceptions=True))


async def kill_process_tree(proc: asyncio.subprocess.Process | None) -> None:
    if proc is None:
        return
    if sys.platform == "win32":
        if proc.returncode is None:
            killer = await asyncio.create_subprocess_exec(
                "taskkill", "/PID", str(proc.pid), "/T", "/F",
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
            await killer.wait()
    else:
        # The session group can still contain descendants after the parent exits.
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    # wait() can hang after SIGKILL when a PIPE transport is paused with a
    # full buffer. Drain it as well so the subprocess transport can close.
    await proc.communicate()


async def spawn_process(
    *argv: str,
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
    stdout: int | IO[str] | IO[bytes] | None = None,
    stderr: int | IO[str] | IO[bytes] | None = None,
) -> asyncio.subprocess.Process:
    """Start an owned session, including when cancellation races process creation."""
    creation = asyncio.create_task(asyncio.create_subprocess_exec(
        *argv, cwd=cwd, env=env, stdout=stdout, stderr=stderr,
        start_new_session=sys.platform != "win32",
    ))
    try:
        return await asyncio.shield(creation)
    except asyncio.CancelledError:
        proc = await creation
        await kill_process_tree(proc)
        raise


async def communicate_process(
    proc: asyncio.subprocess.Process, timeout: float | None = None,
) -> tuple[bytes, bytes]:
    """Capture output, terminating and reaping the session on timeout/cancellation."""
    try:
        async with asyncio.timeout(timeout):
            out, err = await proc.communicate()
        return out or b"", err or b""
    except (asyncio.CancelledError, TimeoutError):
        await kill_process_tree(proc)
        raise
