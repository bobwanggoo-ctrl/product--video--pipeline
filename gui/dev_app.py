"""
Dev GUI — 开发测试版界面
功能：
  - 导入已有任务（从 checkpoint 恢复，跳过已完成步骤）
  - 单步执行（只跑指定 Skill）
  - 完整错误堆栈显示
  - 实时日志（含时间戳）
  - 快速重置单步
  - 任务耗时统计
"""

import json
import logging
import shutil
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QThread, Signal, Qt, QTimer
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QTextEdit, QComboBox, QFileDialog,
    QGroupBox, QSplitter, QCheckBox, QLineEdit, QScrollArea,
    QFrame, QTabWidget,
)
from PySide6.QtGui import QFont, QColor

sys.path.insert(0, str(Path(__file__).parent))


# ── Worker ────────────────────────────────────────────────────

class DevWorker(QThread):
    log_line   = Signal(str)
    step_done  = Signal(str, float)   # step_name, elapsed_sec
    step_fail  = Signal(str, str)     # step_name, full_traceback
    all_done   = Signal(dict)
    progress   = Signal(str, str, str)

    def __init__(self, mode: str, task_id: str, checkpoint_path: str = "",
                 only_step: str = "", video_model: str = "kling", kling_mode: str = "std"):
        super().__init__()
        self.mode = mode                    # "full" | "resume" | "single"
        self.task_id = task_id
        self.checkpoint_path = checkpoint_path
        self.only_step = only_step
        self.video_model = video_model
        self.kling_mode = kling_mode
        self._stop = False

    def run(self):
        handler = _SignalHandler(self.log_line)
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                                               "%H:%M:%S"))
        root_logger = logging.getLogger()
        root_logger.addHandler(handler)
        root_logger.setLevel(logging.DEBUG)

        try:
            self._execute()
        except Exception as e:
            tb = traceback.format_exc()
            self.step_fail.emit("pipeline", tb)
        finally:
            root_logger.removeHandler(handler)

    def _execute(self):
        from config.settings import create_run_dirs, INPUT_DIR, MUSIC_DIR, FONTS_DIR, FCP_TITLES_DIR
        from pipeline.orchestrator import PipelineOrchestrator, PipelineState, StepStatus

        # ── 构建 run_dirs ──
        run_dirs = create_run_dirs(self.task_id)

        # ── 加载或新建 state ──
        if self.mode == "resume" and self.checkpoint_path:
            state = PipelineState.load(Path(self.checkpoint_path))
            self.log_line.emit(f"[Dev] 从 checkpoint 恢复: {self.checkpoint_path}")
            for k, v in state.steps.items():
                self.log_line.emit(f"  {k}: {v.status.value}")
        elif self.mode == "single" and self.only_step:
            state = PipelineState.load(Path(self.checkpoint_path)) if self.checkpoint_path else PipelineState(task_id=self.task_id)
            # 只重置目标步骤
            if self.only_step in state.steps:
                state.steps[self.only_step].status = StepStatus.PENDING
                state.steps[self.only_step].output_data = None
                state.steps[self.only_step].error = None
            self.log_line.emit(f"[Dev] 单步模式: 只跑 {self.only_step}")
        else:
            state = PipelineState(task_id=self.task_id, mode="full_auto")

        # ── 读取 initial_input ──
        input_dir = INPUT_DIR / self.task_id
        sellpoint_file = input_dir / "sellpoint.txt"
        sellpoint = sellpoint_file.read_text(encoding="utf-8") if sellpoint_file.exists() else ""

        initial_input = {
            "sellpoint_text": sellpoint,
            "task_name": self.task_id,
            "video_model": self.video_model,
            "kling_mode": self.kling_mode,
            "reference_image_dir": str(input_dir),
            "bgm_dir": str(MUSIC_DIR) if MUSIC_DIR.exists() else "",
            "font_dir": str(FONTS_DIR) if FONTS_DIR.exists() else "",
            "title_templates_dir": str(FCP_TITLES_DIR) if FCP_TITLES_DIR.exists() else "",
        }

        orchestrator = PipelineOrchestrator(state)
        step_timers: dict[str, float] = {}

        def _on_progress(step, status, detail):
            self.progress.emit(step, status, detail)
            if status == "started":
                step_timers[step] = time.perf_counter()
                self.log_line.emit(f"\n{'='*50}\n▶ {step} 开始\n{'='*50}")
            elif status == "completed":
                elapsed = time.perf_counter() - step_timers.get(step, time.perf_counter())
                self.step_done.emit(step, elapsed)
                self.log_line.emit(f"✓ {step} 完成 ({elapsed:.1f}s)")
            elif status == "failed":
                self.log_line.emit(f"✗ {step} 失败: {detail}")
            elif status == "skipped":
                self.log_line.emit(f"○ {step} 跳过（已完成）")

        try:
            result = orchestrator.run_all(
                initial_input, run_dirs,
                on_progress=_on_progress,
                should_stop=lambda: self._stop,
            )
            self.all_done.emit(result)
        except Exception as e:
            tb = traceback.format_exc()
            self.step_fail.emit(orchestrator.state.current_step or "unknown", tb)

    def stop(self):
        self._stop = True


class _SignalHandler(logging.Handler):
    def __init__(self, signal):
        super().__init__()
        self._sig = signal

    def emit(self, record):
        try:
            self._sig.emit(self.format(record))
        except Exception:
            pass


# ── Main Dev Window ───────────────────────────────────────────

STEP_NAMES = [
    "sellpoint_to_storyboard",
    "storyboard_to_frame",
    "compliance_check",
    "frame_selection",
    "frame_to_video",
    "auto_edit",
]

STEP_LABELS = {
    "sellpoint_to_storyboard": "Skill 1 分镜策划",
    "storyboard_to_frame":     "Skill 2 帧图生成",
    "compliance_check":        "Skill 3 合规检查",
    "frame_selection":         "选材",
    "frame_to_video":          "Skill 4 视频生成",
    "auto_edit":               "Skill 5 自动剪辑",
}


class DevWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Dev GUI — 测试版")
        self.setMinimumSize(1100, 700)
        self._worker: DevWorker | None = None
        self._step_times: dict[str, float] = {}
        self._task_start: float = 0.0
        self._setup_ui()
        self._scan_tasks()

    def _setup_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        main = QHBoxLayout(root)
        main.setContentsMargins(10, 10, 10, 10)
        main.setSpacing(10)

        # ── 左侧控制面板 ──
        left = QWidget()
        left.setFixedWidth(320)
        left_lay = QVBoxLayout(left)
        left_lay.setSpacing(8)

        # 任务选择
        task_grp = QGroupBox("任务")
        task_lay = QVBoxLayout(task_grp)
        self.task_combo = QComboBox()
        task_lay.addWidget(QLabel("已有任务（从 output/ 扫描）:"))
        task_lay.addWidget(self.task_combo)
        self.task_id_edit = QLineEdit()
        self.task_id_edit.setPlaceholderText("或手动输入 task_id")
        task_lay.addWidget(self.task_id_edit)
        self.cp_label = QLabel("checkpoint: 未选择")
        self.cp_label.setWordWrap(True)
        self.cp_label.setStyleSheet("color: #666; font-size: 10px;")
        task_lay.addWidget(self.cp_label)
        btn_row = QHBoxLayout()
        self.btn_load = QPushButton("加载 checkpoint")
        self.btn_load.clicked.connect(self._load_checkpoint)
        btn_row.addWidget(self.btn_load)
        btn_browse = QPushButton("浏览...")
        btn_browse.clicked.connect(self._browse_checkpoint)
        btn_row.addWidget(btn_browse)
        task_lay.addLayout(btn_row)
        left_lay.addWidget(task_grp)

        # 运行模式
        mode_grp = QGroupBox("运行模式")
        mode_lay = QVBoxLayout(mode_grp)
        self.btn_full    = QPushButton("▶ 全流程（新任务）")
        self.btn_resume  = QPushButton("⏩ 从 checkpoint 恢复")
        self.btn_full.clicked.connect(lambda: self._run("full"))
        self.btn_resume.clicked.connect(lambda: self._run("resume"))
        mode_lay.addWidget(self.btn_full)
        mode_lay.addWidget(self.btn_resume)
        left_lay.addWidget(mode_grp)

        # 单步执行
        step_grp = QGroupBox("单步执行")
        step_lay = QVBoxLayout(step_grp)
        self.step_combo = QComboBox()
        for k, v in STEP_LABELS.items():
            self.step_combo.addItem(v, k)
        step_lay.addWidget(self.step_combo)
        self.btn_single = QPushButton("▶ 只跑这一步")
        self.btn_single.clicked.connect(lambda: self._run("single"))
        step_lay.addWidget(self.btn_single)
        left_lay.addWidget(step_grp)

        # 模型选择
        model_grp = QGroupBox("视频模型")
        model_lay = QVBoxLayout(model_grp)
        self.model_combo = QComboBox()
        for label, vm, km in [
            ("KLING-STD", "kling", "std"),
            ("KLING-PRO", "kling", "pro"),
            ("VEO-STD",   "veo_fast", ""),
            ("VEO-4K",    "veo_hq",   ""),
        ]:
            self.model_combo.addItem(label, (vm, km))
        model_lay.addWidget(self.model_combo)
        left_lay.addWidget(model_grp)

        # 步骤状态
        status_grp = QGroupBox("步骤状态")
        status_lay = QVBoxLayout(status_grp)
        self._step_labels: dict[str, QLabel] = {}
        for k, v in STEP_LABELS.items():
            row = QHBoxLayout()
            dot = QLabel("○")
            dot.setFixedWidth(16)
            lbl = QLabel(v)
            time_lbl = QLabel("")
            time_lbl.setStyleSheet("color: #888; font-size: 10px;")
            time_lbl.setFixedWidth(60)
            row.addWidget(dot)
            row.addWidget(lbl, 1)
            row.addWidget(time_lbl)
            status_lay.addLayout(row)
            self._step_labels[k] = (dot, time_lbl)
        left_lay.addWidget(status_grp)

        # 停止按钮
        self.btn_stop = QPushButton("■ 停止")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._stop)
        self.btn_stop.setStyleSheet("background: #FF3B30; color: white; font-weight: bold;")
        left_lay.addWidget(self.btn_stop)

        left_lay.addStretch()
        main.addWidget(left)

        # ── 右侧日志 ──
        right = QWidget()
        right_lay = QVBoxLayout(right)
        right_lay.setSpacing(4)

        tabs = QTabWidget()

        # 日志 tab
        self.log_edit = QTextEdit()
        self.log_edit.setReadOnly(True)
        self.log_edit.setFont(QFont("Menlo", 11))
        self.log_edit.setStyleSheet("background: #1E1E1E; color: #D4D4D4;")
        tabs.addTab(self.log_edit, "日志")

        # 错误 tab
        self.err_edit = QTextEdit()
        self.err_edit.setReadOnly(True)
        self.err_edit.setFont(QFont("Menlo", 11))
        self.err_edit.setStyleSheet("background: #2D0000; color: #FF8080;")
        tabs.addTab(self.err_edit, "错误堆栈")

        # 耗时 tab
        self.time_edit = QTextEdit()
        self.time_edit.setReadOnly(True)
        self.time_edit.setFont(QFont("Menlo", 11))
        tabs.addTab(self.time_edit, "耗时统计")

        right_lay.addWidget(tabs)

        # 底部状态栏
        status_row = QHBoxLayout()
        self.status_lbl = QLabel("就绪")
        self.status_lbl.setStyleSheet("color: #666;")
        btn_clear = QPushButton("清空日志")
        btn_clear.clicked.connect(self._clear_logs)
        btn_open = QPushButton("打开输出目录")
        btn_open.clicked.connect(self._open_output)
        status_row.addWidget(self.status_lbl, 1)
        status_row.addWidget(btn_clear)
        status_row.addWidget(btn_open)
        right_lay.addLayout(status_row)

        main.addWidget(right, 1)

    def _scan_tasks(self):
        """扫描 output/ 目录，填充任务下拉。"""
        self.task_combo.clear()
        output_dir = Path("output")
        if not output_dir.exists():
            return
        tasks = sorted(
            [d for d in output_dir.iterdir() if d.is_dir() and not d.name.startswith(".")],
            key=lambda d: d.stat().st_mtime, reverse=True,
        )
        for t in tasks:
            cp = t / "附件" / "checkpoint.json"
            if not cp.exists():
                cp = t / f"{t.name}-files" / "checkpoint.json"
            label = t.name + (" ✓" if cp.exists() else "")
            self.task_combo.addItem(label, str(t.name))

    def _load_checkpoint(self):
        task_id = self.task_combo.currentData() or self.task_id_edit.text().strip()
        if not task_id:
            return
        # 找 checkpoint
        for sub in ["附件", f"{task_id}-files"]:
            cp = Path("output") / task_id / sub / "checkpoint.json"
            if cp.exists():
                self._set_checkpoint(str(cp), task_id)
                return
        self.cp_label.setText("找不到 checkpoint.json")

    def _browse_checkpoint(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择 checkpoint.json", "output", "JSON (*.json)")
        if path:
            task_id = Path(path).parent.parent.name
            self._set_checkpoint(path, task_id)

    def _set_checkpoint(self, cp_path: str, task_id: str):
        self._cp_path = cp_path
        self.cp_label.setText(f"checkpoint: {cp_path}")
        self.task_id_edit.setText(task_id)
        # 显示步骤状态
        try:
            d = json.loads(Path(cp_path).read_text())
            for k, v in d.get("steps", {}).items():
                if k in self._step_labels:
                    dot, _ = self._step_labels[k]
                    status = v.get("status", "pending")
                    dot.setText({"completed": "✓", "failed": "✗", "skipped": "○"}.get(status, "…"))
                    dot.setStyleSheet({"completed": "color:green", "failed": "color:red"}.get(status, ""))
        except Exception:
            pass

    def _run(self, mode: str):
        task_id = self.task_id_edit.text().strip() or self.task_combo.currentData()
        if not task_id:
            self._log("❌ 请先选择或输入 task_id")
            return

        cp_path = getattr(self, "_cp_path", "")
        only_step = self.step_combo.currentData() if mode == "single" else ""
        vm, km = self.model_combo.currentData()

        self._worker = DevWorker(mode, task_id, cp_path, only_step, vm, km)
        self._worker.log_line.connect(self._log)
        self._worker.step_done.connect(self._on_step_done)
        self._worker.step_fail.connect(self._on_step_fail)
        self._worker.all_done.connect(self._on_all_done)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(lambda: self.btn_stop.setEnabled(False))

        self._task_start = time.perf_counter()
        self._step_times.clear()
        self.err_edit.clear()
        self.time_edit.clear()
        self.btn_stop.setEnabled(True)
        self.status_lbl.setText(f"运行中: {task_id} [{mode}]")
        self._log(f"\n{'='*60}\n{datetime.now().strftime('%H:%M:%S')} 开始 {mode} — {task_id}\n{'='*60}")
        self._worker.start()

    def _stop(self):
        if self._worker:
            self._worker.stop()
            self.status_lbl.setText("正在停止...")

    def _on_progress(self, step, status, detail):
        if status == "started":
            self._step_times[step] = time.perf_counter()
            if step in self._step_labels:
                dot, _ = self._step_labels[step]
                dot.setText("→")
                dot.setStyleSheet("color: #007AFF; font-weight: bold;")
        elif status == "completed":
            elapsed = time.perf_counter() - self._step_times.get(step, time.perf_counter())
            if step in self._step_labels:
                dot, time_lbl = self._step_labels[step]
                dot.setText("✓")
                dot.setStyleSheet("color: green;")
                time_lbl.setText(f"{elapsed:.1f}s")
        elif status == "failed":
            if step in self._step_labels:
                dot, _ = self._step_labels[step]
                dot.setText("✗")
                dot.setStyleSheet("color: red;")

    def _on_step_done(self, step: str, elapsed: float):
        self._step_times[step] = elapsed
        self._update_time_tab()

    def _on_step_fail(self, step: str, tb: str):
        self.err_edit.append(f"{'='*50}\n✗ {step} 失败\n{'='*50}\n{tb}\n")
        self._log(f"✗ {step} 失败 — 详见「错误堆栈」tab")
        self.status_lbl.setText(f"✗ {step} 失败")

    def _on_all_done(self, result: dict):
        total = time.perf_counter() - self._task_start
        self._log(f"\n{'='*60}\n✓ 全部完成！总耗时: {total:.1f}s ({total/60:.1f}min)\n{'='*60}")
        self.status_lbl.setText(f"✓ 完成 ({total:.1f}s)")
        self._update_time_tab()
        if result.get("mp4"):
            self._log(f"  MP4: {result['mp4']}")

    def _update_time_tab(self):
        lines = ["步骤耗时统计\n" + "="*40]
        total = 0.0
        for step in STEP_NAMES:
            t = self._step_times.get(step)
            if t:
                lines.append(f"  {STEP_LABELS.get(step, step):<25} {t:>7.1f}s")
                total += t
        if total:
            lines.append(f"\n  {'总计':<25} {total:>7.1f}s ({total/60:.1f}min)")
        self.time_edit.setPlainText("\n".join(lines))

    def _log(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_edit.append(f"[{ts}] {msg}")
        # 自动滚到底
        sb = self.log_edit.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _clear_logs(self):
        self.log_edit.clear()
        self.err_edit.clear()
        self.time_edit.clear()

    def _open_output(self):
        task_id = self.task_id_edit.text().strip() or self.task_combo.currentData()
        if task_id:
            import subprocess, platform
            p = Path("output") / task_id
            if p.exists():
                if platform.system() == "Darwin":
                    subprocess.Popen(["open", str(p)])


def main():
    app = QApplication(sys.argv)
    win = DevWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
