"""Shared shutdown wait loop for dedicated long-running service processes."""

from __future__ import annotations

import asyncio
import signal


async def wait_for_shutdown() -> None:
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    installed: list[signal.Signals] = []
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, stopped.set)
        except (NotImplementedError, RuntimeError):
            continue
        installed.append(signum)
    try:
        await stopped.wait()
    finally:
        for signum in installed:
            loop.remove_signal_handler(signum)


__all__ = ["wait_for_shutdown"]
