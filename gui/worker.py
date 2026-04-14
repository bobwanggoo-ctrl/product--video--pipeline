"""Background worker — runs the pipeline in a QThread."""

import logging
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

    def __init__(self, sellpoint_text: str, image_paths: list[str]):
        super().__init__()
        self.sellpoint_text = sellpoint_text
        self.image_paths = image_paths
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

        task_id = f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
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
            "reference_image_dir": str(input_dir),
            "bgm_dir": str(MUSIC_DIR) if MUSIC_DIR.exists() else "",
            "font_dir": str(FONTS_DIR) if FONTS_DIR.exists() else "",
            "title_templates_dir": str(FCP_TITLES_DIR) if FCP_TITLES_DIR.exists() else "",
        }

        state = PipelineOrchestrator.__new__(PipelineOrchestrator)  # avoid double import
        from pipeline.orchestrator import PipelineOrchestrator, PipelineState
        state = PipelineState(task_id=task_id, mode="full_auto")
        orchestrator = PipelineOrchestrator(state)

        result = orchestrator.run_all(
            initial_input,
            run_dirs,
            on_progress=lambda step, status, detail: self.progress.emit(step, status, detail),
            should_stop=self._stop_event.is_set,
        )

        result["output_dir"] = str(run_dirs["root"])
        return result


# ── Logging bridge ──────────────────────────────────────────

class _SignalLogHandler(logging.Handler):
    def __init__(self, signal):
        super().__init__()
        self._signal = signal

    def emit(self, record):
        try:
            self._signal.emit(self.format(record))
        except Exception:
            pass
