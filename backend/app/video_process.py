"""Local worker supervisor: parent pipe EOF kills its complete process group.

The random token and receipt identify this supervisor before recovery can kill
it. A PID alone is never treated as proof of ownership.
"""

from __future__ import annotations
import argparse, asyncio, json, os, signal, subprocess, sys, tempfile, threading, time, uuid
from collections.abc import Callable, Mapping
from pathlib import Path
from pydantic import BaseModel, Field, ValidationError


class WorkerIdentity(BaseModel):
    pid: int = Field(ge=1)
    token: str = Field(pattern=r"^[0-9a-f]{32}$")
    receipt: str


class WorkerReceipt(BaseModel):
    pid: int
    token: str
    returncode: int | None = None


class WorkerOutputError(Exception):
    """A captured worker stream is missing or exceeds its permitted size."""


async def read_owned_output(
    proc: asyncio.subprocess.Process, *, max_bytes: int, timeout: float
) -> bytes:
    """Collect stdout through EOF without closing the supervisor stdin pipe.

    The process owner must terminate and reap the worker on failure, including
    output overflow: waiting without draining a full pipe can otherwise hang.
    """
    if max_bytes < 1:
        raise ValueError("invalid_output_limit")
    if proc.stdout is None:
        raise WorkerOutputError("worker_stdout_unavailable")
    output = bytearray()
    async with asyncio.timeout(timeout):
        while chunk := await proc.stdout.read(min(65536, max_bytes - len(output) + 1)):
            if len(output) + len(chunk) > max_bytes:
                raise WorkerOutputError("worker_output_too_large")
            output.extend(chunk)
        await proc.wait()
    return bytes(output)


def _receipt(path: Path, value: WorkerReceipt) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", dir=path.parent, delete=False, encoding="utf-8"
        ) as handle:
            temporary = Path(handle.name)
            handle.write(value.model_dump_json())
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _supervise() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--token", required=True)
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--cwd")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command: list[str] = args.command
    if command and command[0] == "--":
        command = command[1:]
    value = WorkerReceipt(pid=os.getpid(), token=args.token)
    receipt = Path(args.receipt)
    _receipt(receipt, value)

    def watch_parent() -> None:
        os.read(sys.stdin.fileno(), 1)
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/PID", str(os.getpid()), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            os._exit(99)
        os.killpg(os.getpgrp(), signal.SIGKILL)

    threading.Thread(target=watch_parent, daemon=True).start()
    try:
        proc = subprocess.Popen(command, cwd=args.cwd)
        value.returncode = proc.wait()
    except Exception:
        value.returncode = 1
    _receipt(receipt, value)
    return value.returncode


async def spawn_owned(
    argv: list[str],
    *,
    receipt_path: Path,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    stdout: int | None = None,
    on_identity: Callable[[WorkerIdentity], None] | None = None,
) -> asyncio.subprocess.Process:
    from .job_lifecycle import await_cleanup, kill_process_tree

    token = uuid.uuid4().hex
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--token",
        token,
        "--receipt",
        str(receipt_path.resolve()),
    ]
    if cwd is not None:
        command += ["--cwd", str(cwd)]
    command += ["--", *argv]
    creation = asyncio.create_task(
        asyncio.create_subprocess_exec(
            *command,
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=stdout,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=sys.platform != "win32",
        )
    )
    try:
        proc = await asyncio.shield(creation)
        if on_identity is not None:
            on_identity(
                WorkerIdentity(pid=proc.pid, token=token, receipt=str(receipt_path))
            )
        return proc
    except BaseException:
        proc = await await_cleanup(creation)
        await await_cleanup(kill_process_tree(proc))
        raise


async def terminate_verified(identity: WorkerIdentity) -> bool:
    """False means a live process could not be verified; never kill that PID."""
    from .job_lifecycle import spawn_process, communicate_process

    if sys.platform == "win32":
        command = [
            "powershell",
            "-NoProfile",
            "-Command",
            f'(Get-CimInstance Win32_Process -Filter "ProcessId = {identity.pid}").CommandLine',
        ]
    else:
        command = ["ps", "-p", str(identity.pid), "-o", "command="]
    proc = await spawn_process(
        *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    out, _ = await communicate_process(proc, 5)
    text = out.decode(errors="replace").strip()
    if not text:
        return True
    if (
        str(Path(__file__).resolve()) not in text
        or f"--token {identity.token}" not in text
    ):
        return False
    try:
        receipt = WorkerReceipt.model_validate_json(Path(identity.receipt).read_bytes())
    except (OSError, ValidationError):
        return False
    if receipt.pid != identity.pid or receipt.token != identity.token:
        return False
    if sys.platform == "win32":
        killer = await spawn_process(
            "taskkill",
            "/PID",
            str(identity.pid),
            "/T",
            "/F",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await communicate_process(killer, 10)
        return killer.returncode == 0
    try:
        if os.getpgid(identity.pid) != identity.pid:
            return False
        os.killpg(identity.pid, signal.SIGKILL)
    except ProcessLookupError:
        return True
    for _ in range(100):
        try:
            os.kill(identity.pid, 0)
        except ProcessLookupError:
            return True
        await asyncio.sleep(0.02)
    return False


if __name__ == "__main__":
    raise SystemExit(_supervise())
