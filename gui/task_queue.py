"""AppTaskQueue — pipeline 级并发队列。

限制同一 app 实例中同时运行的 PipelineWorker 数量。
用户点击"开始"后 worker 立即加入队列；如果当前运行数未达上限则
立即启动，否则在后台线程中等待空槽（不阻塞 GUI 线程）。

运行时可通过 set_limit(n) 调整上限，立即生效。
"""

import threading

from utils.dynamic_semaphore import DynamicSemaphore


class AppTaskQueue:
    def __init__(self, max_running: int):
        self._sem = DynamicSemaphore(max_running)

    # ── 公开接口 ──────────────────────────────────────

    @property
    def limit(self) -> int:
        return self._sem.limit

    @property
    def active(self) -> int:
        return self._sem.active

    def set_limit(self, n: int) -> None:
        """运行时调整最大并发 pipeline 数，立即生效。"""
        self._sem.set_limit(n)

    def submit(self, worker, *, on_started=None) -> None:
        """将 worker 加入队列。

        在独立守护线程中等待信号量，不阻塞调用方（GUI 线程）。
        获得槽位后调用 worker.start()，然后回调 on_started（可选）。
        """
        threading.Thread(
            target=self._wait_and_start,
            args=(worker, on_started),
            daemon=True,
            name=f"TaskQueueSlot-{id(worker)}",
        ).start()

    # ── 内部 ─────────────────────────────────────────

    def _wait_and_start(self, worker, on_started) -> None:
        self._sem.acquire()
        # 当 QThread 完成后释放信号量槽位
        worker.finished.connect(self._sem.release)
        worker.start()
        if on_started:
            on_started()
