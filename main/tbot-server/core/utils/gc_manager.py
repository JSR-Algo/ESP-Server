"""
Global GC management module
Periodically run garbage collection to avoid GIL lock issues caused by frequent GC triggers
"""

import gc
import asyncio
import threading
from config.logger import setup_logging

TAG = __name__
logger = setup_logging()


class GlobalGCManager:
    """Global garbage collection manager"""

    def __init__(self, interval_seconds=300):
        """
        Initialize GC manager

        Args:
            interval_seconds: GC run interval (seconds), default 300 seconds (5 minutes)
        """
        self.interval_seconds = interval_seconds
        self._task = None
        self._stop_event = asyncio.Event()
        self._lock = threading.Lock()

    async def start(self):
        """Start scheduled GC task"""
        if self._task is not None:
            logger.bind(tag=TAG).warning("GC manager already running")
            return

        logger.bind(tag=TAG).info(f"Start global GC manager, interval {self.interval_seconds} seconds")
        self._stop_event.clear()
        self._task = asyncio.create_task(self._gc_loop())

    async def stop(self):
        """Stop scheduled GC task"""
        if self._task is None:
            return

        logger.bind(tag=TAG).info("Stop global GC manager")
        self._stop_event.set()

        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        self._task = None

    async def _gc_loop(self):
        """GC loop task"""
        try:
            while not self._stop_event.is_set():
                # Wait specified interval
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(), timeout=self.interval_seconds
                    )
                    # Ifstop_eventSet, exit loop
                    break
                except asyncio.TimeoutError:
                    # Timeout means reached executionGCTime of
                    pass

                # ExecuteGC
                await self._run_gc()

        except asyncio.CancelledError:
            logger.bind(tag=TAG).info("GC loop task canceled")
            raise
        except Exception as e:
            logger.bind(tag=TAG).error(f"GC loop task exception: {e}")
        finally:
            logger.bind(tag=TAG).info("GC loop task exited")

    async def _run_gc(self):
        """Execute garbage collection"""
        try:
            # Execute in thread poolGC, avoid blockingEventLoop
            loop = asyncio.get_running_loop()

            def do_gc():
                with self._lock:
                    before = len(gc.get_objects())
                    collected = gc.collect()
                    after = len(gc.get_objects())
                    return before, collected, after

            before, collected, after = await loop.run_in_executor(None, do_gc)
            logger.bind(tag=TAG).debug(
                f"Global GC completed - objects collected: {collected}, "
                f"Object count: {before} -> {after}"
            )
        except Exception as e:
            logger.bind(tag=TAG).error(f"Error running GC: {e}")


# Global Singleton
_gc_manager_instance = None


def get_gc_manager(interval_seconds=300):
    """
    Get global GC manager instance (singleton mode)

    Args:
        interval_seconds: GC run interval (seconds), default 300 seconds (5 minutes)

    Returns:
        GlobalGCManager instance
    """
    global _gc_manager_instance
    if _gc_manager_instance is None:
        _gc_manager_instance = GlobalGCManager(interval_seconds)
    return _gc_manager_instance
