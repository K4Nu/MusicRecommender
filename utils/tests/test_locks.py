import pytest
from django.core.cache import cache

from utils.locks import ResourceLock, ResourceLockedException


@pytest.fixture(autouse=True)
def clear_cache_before_each():
    """Ensure a clean cache state for every test."""
    cache.clear()
    yield
    cache.clear()


# =========================================================
# 1. acquire / release
# =========================================================

class TestAcquireRelease:

    def test_acquire_returns_true_when_free(self):
        lock = ResourceLock("pipeline", "user_1")
        assert lock.acquire() is True
        lock.release()

    def test_acquire_returns_false_when_already_locked(self):
        lock1 = ResourceLock("pipeline", "user_2")
        lock2 = ResourceLock("pipeline", "user_2")
        lock1.acquire()
        assert lock2.acquire() is False
        lock1.release()

    def test_release_allows_reacquire(self):
        lock = ResourceLock("pipeline", "user_3")
        lock.acquire()
        lock.release()
        assert lock.acquire() is True
        lock.release()

    def test_different_ids_are_independent(self):
        lock_a = ResourceLock("pipeline", "user_a")
        lock_b = ResourceLock("pipeline", "user_b")
        lock_a.acquire()
        assert lock_b.acquire() is True
        lock_a.release()
        lock_b.release()

    def test_different_resource_types_are_independent(self):
        lock1 = ResourceLock("pipeline", "res_1")
        lock2 = ResourceLock("task", "res_1")   # same id, different type
        lock1.acquire()
        assert lock2.acquire() is True
        lock1.release()
        lock2.release()


# =========================================================
# 2. is_locked
# =========================================================

class TestIsLocked:

    def test_false_when_not_acquired(self):
        lock = ResourceLock("pipeline", "check_1")
        assert lock.is_locked() is False

    def test_true_after_acquire(self):
        lock = ResourceLock("pipeline", "check_2")
        lock.acquire()
        assert lock.is_locked() is True
        lock.release()

    def test_false_after_release(self):
        lock = ResourceLock("pipeline", "check_3")
        lock.acquire()
        lock.release()
        assert lock.is_locked() is False


# =========================================================
# 3. get_lock_info
# =========================================================

class TestGetLockInfo:

    def test_returns_none_when_free(self):
        lock = ResourceLock("pipeline", "info_1")
        assert lock.get_lock_info() is None

    def test_returns_dict_with_correct_fields_when_locked(self):
        lock = ResourceLock("pipeline", "info_2")
        lock.acquire()
        info = lock.get_lock_info()
        assert info is not None
        assert info["resource_type"] == "pipeline"
        assert info["resource_id"] == "info_2"
        assert "acquired_at" in info
        lock.release()

    def test_returns_none_after_release(self):
        lock = ResourceLock("pipeline", "info_3")
        lock.acquire()
        lock.release()
        assert lock.get_lock_info() is None


# =========================================================
# 4. Context manager
# =========================================================

class TestContextManager:

    def test_releases_lock_on_normal_exit(self):
        lock = ResourceLock("pipeline", "ctx_1")
        with lock:
            assert lock.is_locked() is True
        assert lock.is_locked() is False

    def test_releases_lock_on_exception(self):
        lock = ResourceLock("pipeline", "ctx_2")
        try:
            with lock:
                raise ValueError("something went wrong")
        except ValueError:
            pass
        assert lock.is_locked() is False

    def test_raises_resource_locked_exception_when_already_locked(self):
        lock1 = ResourceLock("pipeline", "ctx_3")
        lock2 = ResourceLock("pipeline", "ctx_3")
        lock1.acquire()
        with pytest.raises(ResourceLockedException):
            with lock2:
                pass
        lock1.release()

    def test_exception_message_contains_resource_info(self):
        lock1 = ResourceLock("my_task", "job_99")
        lock2 = ResourceLock("my_task", "job_99")
        lock1.acquire()
        with pytest.raises(ResourceLockedException, match="my_task"):
            with lock2:
                pass
        lock1.release()
