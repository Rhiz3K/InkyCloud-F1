"""Event-loop-scoped synchronization helpers."""

import asyncio
import weakref
from collections.abc import Hashable

LoopLockRegistry = weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock]
KeyedLoopLockRegistry = weakref.WeakKeyDictionary[
    asyncio.AbstractEventLoop, dict[Hashable, asyncio.Lock]
]


def get_loop_lock(registry: LoopLockRegistry) -> asyncio.Lock:
    """Return one lock per running event loop for the supplied job registry."""
    loop = asyncio.get_running_loop()
    lock = registry.get(loop)
    if lock is None:
        lock = asyncio.Lock()
        registry[loop] = lock
    return lock


def get_keyed_loop_lock(registry: KeyedLoopLockRegistry, key: Hashable) -> asyncio.Lock:
    """Return one lock per running event loop and key, coalescing concurrent fetches."""
    loop = asyncio.get_running_loop()
    locks = registry.setdefault(loop, {})
    return locks.setdefault(key, asyncio.Lock())
