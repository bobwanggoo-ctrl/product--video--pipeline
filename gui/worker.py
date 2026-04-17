"""Background worker — runs the pipeline in a QThread."""

import logging
import re
import shutil
import threading
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QThread, Signal


class PipelineWorker(QThread):
    """Executes the full pipeline in a background thread.

    Signals:
        progress(step_name, status, detail)
        log(message)
        run_dirs_ready(output_dir)  — emitted as soon as run dirs are created
        finished(result_dict)
        error(message)
    """

    progress       = Signal(str, str, str)
    log            = Signal(str)
    run_dirs_ready = Signal(str)   # output root path, for Files button
    pipeline_done  = Signal(dict)   # renamed: avoids shadowing QThread.finished
    error          = Signal(str)

    def __init__(self, sellpoint_text: str, image_paths: list[str], task_name: str = "",
                 video_model: str = "kling", kling_mode: str = "std",
                 resume_from_checkpoint: str = ""):
        super().__init__()
        self.sellpoint_text = sellpoint_text
        self.image_paths = image_paths
        self.video_model = video_model
        self.kling_mode  = kling_mode
        self.resume_from_checkpoint = resume_from_checkpoint
        # Sanitize task_name for filesystem; fall back to timestamp
        safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '', task_name).strip('. ')
        self.task_name = safe if safe else f"task_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        self._stop_event = threading.Event()

    # ── Qt entry point ──────────────────────────────────────

    def run(self):
        handler = _SignalLogHandler(self.log)
        handler.setFormatter(logging.Formatter("%(asctime)s  %(message)s", "%H:%M:%S"))
        root_logger = logging.getLogger()
        root_logger.addHandler(handler)
        try:
            result = self._run_pipeline()
            self.pipeline_done.emit(result)
        except Exception as exc:
            self.error.emit(str(exc))
        finally:
            root_logger.removeHandler(handler)

    def stop(self):
        """Signal the pipeline to stop after the current step completes."""
        self._stop_event.set()

    # ── Pipeline execution ──────────────────────────────────

    def _run_pipeline(self) -> dict:
        from config.settings import create_run_dirs, INPUT_DIR, MUSIC_DIR, FONTS_DIR, FCP_TITLES_DIR
        from pipeline.orchestrator import PipelineOrchestrator, PipelineState

        task_id = self.task_name
        input_dir = INPUT_DIR / task_id
        input_dir.mkdir(parents=True, exist_ok=True)

        (input_dir / "sellpoint.txt").write_text(self.sellpoint_text, encoding="utf-8")
        for src in self.image_paths:
            shutil.copy2(src, input_dir / Path(src).name)

        run_dirs = create_run_dirs(task_id)
        run_dirs["sellpoint"].write_text(self.sellpoint_text, encoding="utf-8")

        # Emit output path immediately so Files button works during run
        self.run_dirs_ready.emit(str(run_dirs["root"]))

        initial_input = {
            "sellpoint_text": self.sellpoint_text,
            "task_name":  task_id,
            "video_model": self.video_model,
            "kling_mode":  self.kling_mode,
            "reference_image_dir": str(input_dir),
            "bgm_dir": str(MUSIC_DIR) if MUSIC_DIR.exists() else "",
            "font_dir": str(FONTS_DIR) if FONTS_DIR.exists() else "",
            "title_templates_dir": str(FCP_TITLES_DIR) if FCP_TITLES_DIR.exists() else "",
        }

        state = PipelineOrchestrator.__new__(PipelineOrchestrator)  # avoid double import
        from pipeline.orchestrator import PipelineOrchestrator, PipelineState
        state = PipelineState(task_id=task_id, mode="full_auto")

        # Resume from checkpoint if provided
        if self.resume_from_checkpoint:
            cp = Path(self.resume_from_checkpoint)
            if cp.exists():
                state = PipelineState.load(cp)
                self.log.emit(f"⏩ 从断点恢复: {cp.name}")
            else:
                self.log.emit(f"⚠️ checkpoint 不存在，从头开始")

        orchestrator = PipelineOrchestrator(state)

        result = orchestrator.run_all(
            initial_input,
            run_dirs,
            on_progress=lambda step, status, detail: self.progress.emit(step, status, detail),
            should_stop=self._stop_event.is_set,
        )

        result["output_dir"] = str(run_dirs["final"])   # open Final folder after done
        return result


# ── Logging bridge ──────────────────────────────────────────

# 用户友好日志规则：(正则, 替换模板)
# 替换模板中 \1 \2 等对应正则捕获组
import re as _re

_LOG_RULES = [
    # LLM 重试
    (_re.compile(r"\[LLM\].*attempt=(\d+) 失败.*超时.*\((\d+)\.0s\)"),
     lambda m: f"⚠️ AI 任务超时，正在重试（第 {m.group(1)} 次）"),
    (_re.compile(r"\[LLM\].*attempt=(\d+) 失败"),
     lambda m: f"⚠️ AI 请求失败，正在重试（第 {m.group(1)} 次）"),
    # Vision 重试
    (_re.compile(r"\[Vision\].*attempt=(\d+) 失败"),
     lambda m: f"⚠️ 合规检查请求失败，正在重试（第 {m.group(1)} 次）"),
    # LLM 全部失败
    (_re.compile(r"\[Converter\] LLM call failed"),
     lambda m: "❌ 分镜生成失败，请稍后重试"),
    # 分镜校验失败重试
    (_re.compile(r"\[Converter\] 硬约束校验失败 \(attempt (\d+)\)"),
     lambda m: f"⚠️ 分镜校验失败，正在重试（第 {m.group(1)} 次）"),
    # Kling 视频失败
    (_re.compile(r"shot_(\d+) Kling 失败"),
     lambda m: f"⚠️ 视频片段 {int(m.group(1))} 超时，跳过"),
    # 帧图生成失败
    (_re.compile(r"shot_(\d+) 失败"),
     lambda m: f"⚠️ 帧图 {int(m.group(1))} 生成失败"),
    # 帧图提交失败
    (_re.compile(r"shot_(\d+) 提交失败"),
     lambda m: f"⚠️ 帧图 {int(m.group(1))} 提交失败"),
    # 景别不足
    (_re.compile(r"景别 (\S+) 只有 (\d+) 个，低于最低要求 (\d+)"),
     lambda m: f"⚠️ {m.group(1)} 景别视频不足（{m.group(2)}/{m.group(3)}），继续处理"),
]


class _SignalLogHandler(logging.Handler):
    """把 WARNING/ERROR 日志转成用户友好提示发给 GUI；同时把原始信息打到 stderr。"""

    def __init__(self, signal):
        super().__init__()
        self._signal = signal

    def emit(self, record):
        try:
            raw = record.getMessage()

            # 始终把 WARNING/ERROR 原始信息打到终端（调试用）
            if record.levelno >= logging.WARNING:
                import sys
                print(f"[{record.levelname}] {record.name}: {raw}", file=sys.stderr)

            # 只把 WARNING/ERROR 发给 GUI，且转成友好文案
            if record.levelno >= logging.WARNING:
                friendly = self._to_friendly(raw)
                if friendly:
                    self._signal.emit(friendly)
        except Exception:
            pass

    @staticmethod
    def _to_friendly(msg: str) -> str:
        for pattern, formatter in _LOG_RULES:
            m = pattern.search(msg)
            if m:
                return formatter(m)
        # 没有匹配规则的 WARNING/ERROR：截断到 60 字，去掉技术前缀
        # 去掉 [Xxx] 前缀
        cleaned = _re.sub(r"^\[[\w/\[\]]+\]\s*", "", msg).strip()
        return f"⚠️ {cleaned[:80]}" if cleaned else ""
