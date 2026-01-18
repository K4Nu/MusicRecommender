from datetime import time

from django.core.cache import cache
from functools import wraps
import logging

logger = logging.getLogger(__name__)

class ResourceLock():
    """Generic lock manager for any resource type"""

    def __init__(self, resource_type, resource_id, timeout=600):
        self.resource_type = resource_type
        self.resource_id = resource_id
        self.timeout = timeout
        self.key=f'{resource_type}_lock:{resource_id}'

    def acquire(self):
        """Try to acquire lock. Returns True if successful, False if already locked."""
        # cache.add() returns True only if key doesn't exist (atomic operation)
        lock_value={
            "acquired_at": time.time(),
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
        }
        return cache.add(self.key, lock_value, timeout=self.timeout)

    def release(self):
        """Release the lock."""
        cache.delete(self.key)

    def is_locked(self):
        """Check if resource is currently locked"""
        return cache.get(self.key) is not None

    def get_lock_info(self):
        """Get information about current lock (for debugging)"""
        return cache.get(self.key)

    def __enter__(self):
        """Context manager support"""
        if not self.acquire():
            lock_info = self.get_lock_info()
            if lock_info:
                aquired_at = lock_info.get("acquired_at","unknown")
                age=time.time()-aquired_at if isinstance(aquired_at,int) else "unknown"
                logger.warning(
                    f"{self.resource_type} {self.resource_id} locked for {age}s"
                )
            raise ResourceLockedException(
                f"{self.resource_type} {self.resource_id} is already being processed"

            )

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Automatically release lock when exiting context"""
        self.release()
        return False

class ResourceLockedException(Exception):
    """Raised when trying to acquire an already locked resource"""
