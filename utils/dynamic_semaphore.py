"""运行时可调上限的信号量。

与标准 threading.Semaphore 的区别：
  set_limit(n) 可在任意时刻调用，立即生效：
  - 调高 → 等待中的线程可立即竞争新增槽位
  - 调低 → 已持有槽位的线程继续执行，新请求等待降低后的上限
"""

import threading


class DynamicSemaphore:
    def __init__(self, value: int):
        self._cond   = threading.Condition(threading.Lock())
        self._limit  = max(1, value)
        self._active = 0

    # ── 属性 ───────────────────────────────────────────

    @property
    def limit(self) -> int:
        """当前并发上限。"""
        with self._cond:
            return self._limit

    @property
    def active(self) -> int:
        """当前持有者数量。"""
        with self._cond:
            return self._active

    # ── 控制 ───────────────────────────────────────────

    def set_limit(self, new_limit: int) -> None:
        """运行时调整上限，立即通知所有等待线程重新竞争。"""
        with self._cond:
            self._limit = max(1, new_limit)
            self._cond.notify_all()

    def acquire(self) -> None:
        with self._cond:
            while self._active >= self._limit:
                self._cond.wait()
            self._active += 1

    def release(self) -> None:
        with self._cond:
            self._active = max(0, self._active - 1)
            self._cond.notify()

    # ── context manager ────────────────────────────────

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *_):
        self.release()
