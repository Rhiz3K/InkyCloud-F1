"""Event-loop-scoped synchronization helpers."""

import asyncio
import weakref

LoopLockRegistry = weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock]


def get_loop_lock(registry: LoopLockRegistry) -> asyncio.Lock:
    """Return one lock per running event loop for the supplied job registry."""
    loop = asyncio.get_running_loop()
    lock = registry.get(loop)
    if lock is None:
        lock = asyncio.Lock()
        registry[loop] = lock
    return lock
