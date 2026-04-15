"""Product Video Pipeline — GUI entry point.

Apple-inspired design: white background, SF Pro font, rounded cards,
blue accent (#007AFF), generous whitespace.

Layout (matches wireframe):
  ┌────────────────┬──────────────────────────────┐
  │ Image grid     │                              │
  │  (batch/paste) │   Progress / Video player    │
  │ Sellpoint text │        (16:9 default)        │
  ├────────────────┴──────────────────────────────┤
  │ [Start]                            [Files]    │
  └───────────────────────────────────────────────┘
"""

import sys
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import (Qt, QSize, QUrl, QMimeData, QVariantAnimation,
                             QEasingCurve, QRectF, QPointF, QTimer, Signal,
                             QPropertyAnimation)
from PySide6.QtGui import (
    QFont, QPixmap, QDragEnterEvent, QDropEvent, QKeySequence,
    QImage, QPainter, QPainterPath, QPen, QBrush, QLinearGradient, QColor,
    QShortcut,
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QGridLayout, QLabel, QTextEdit, QLineEdit, QPushButton, QFileDialog,
    QProgressBar, QScrollArea, QSizePolicy, QFrame, QStackedWidget,
    QGraphicsDropShadowEffect, QDialog, QSpinBox, QFormLayout,
)
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget

from gui.worker import PipelineWorker
from gui.task_queue import AppTaskQueue


# ── Palette ──────────────────────────────────────────────────
BG              = "#FFFFFF"
CARD_BG         = "#F5F5F7"
ACCENT          = "#007AFF"   # step indicators / progress bar
TEXT_PRIMARY    = "#1D1D1F"
TEXT_MUTED      = "#6E6E73"
BORDER          = "#D2D2D7"
FOCUS_BORDER    = "#AEAEB2"   # card focus ring — light gray
BTN_PRIMARY     = "#3A3A3C"   # start button — dark gray
BTN_PRIMARY_HVR = "#2C2C2E"
SUCCESS         = "#34C759"
KLING_WAITING   = "#FFD60A"   # 可灵排队中 → 黄色进度条
ERROR_COLOR     = "#FF3B30"
STEP_DONE       = "#34C759"
STEP_ACTIVE     = "#007AFF"
STEP_PENDING    = "#D2D2D7"

STEP_LABELS = {
    "sellpoint_to_storyboard": "分镜策划",
    "storyboard_to_frame":     "帧图生成",
    "compliance_check":        "合规审查",
    "frame_selection":         "素材筛选",
    "frame_to_video":          "视频片段",
    "auto_edit":               "智能剪辑",
}
STEP_ORDER = list(STEP_LABELS.keys())
MAX_IMAGES = 6
CELL_W, CELL_H = 110, 88
MAX_TASKS = 15


# ── TaskState: per-task data ─────────────────────────────────
@dataclass
class TaskState:
    name: str
    image_paths: list = field(default_factory=list)
    sellpoint: str = ""
    panel_state: int = 0          # 0 idle  1 running  2 done
    step_states: dict = field(default_factory=dict)
    log_lines: list = field(default_factory=list)
    mp4_path: str = ""
    output_dir: str = ""
    worker: object = None         # PipelineWorker | None


# ── MiniDot: 7px dot for task tabs ───────────────────────────
class MiniDot(QWidget):
    S = 7

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(self.S, self.S)
        self._state = "pending"
        self._pulse = 0.0
        self._pulse_dir = 1
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    def set_state(self, state: str):
        self._state = state
        if state == "active":
            self._timer.start(30)
        else:
            self._timer.stop()
            self._pulse = 0.0
        self.update()

    def _tick(self):
        self._pulse += 0.06 * self._pulse_dir
        if self._pulse >= 1.0:   self._pulse_dir = -1
        elif self._pulse <= 0.0: self._pulse_dir = 1
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        s, cx, cy = self.S, self.S / 2, self.S / 2
        r = s / 2 - 0.5
        if self._state == "pending":
            p.setPen(QPen(QColor(BORDER), 1.0))
            p.setBrush(Qt.NoBrush)
        elif self._state == "active":
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(ACCENT))
            p.setOpacity(0.4 + 0.6 * self._pulse)
        elif self._state in ("done", "skipped"):
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(SUCCESS) if self._state == "done" else QColor(BORDER))
        elif self._state == "failed":
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(ERROR_COLOR))
        else:
            p.setPen(QPen(QColor(BORDER), 1.0))
            p.setBrush(Qt.NoBrush)
        p.drawEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))
        p.end()


# ── TaskTab: single tab card ──────────────────────────────────
class TaskTab(QFrame):
    clicked         = Signal()
    delete_requested = Signal()
    name_changed    = Signal(str)

    TAB_W, TAB_H = 82, 30

    def __init__(self, name: str, parent=None):
        super().__init__(parent)
        self.setFixedSize(self.TAB_W, self.TAB_H)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover)
        self._active          = False
        self._progress        = 0.0
        self._progress_color  = SUCCESS   # 默认绿色；可灵排队时改为 KLING_WAITING
        self._editing         = False

        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 0, 6, 0)
        lay.setSpacing(2)

        self._name_edit = QLineEdit(name)
        self._name_edit.setReadOnly(True)
        self._name_edit.setFrame(False)
        self._name_edit.setAlignment(Qt.AlignCenter)
        self._name_edit.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._name_edit.setStyleSheet(
            "QLineEdit { border: none; background: transparent;"
            f" font-size: 10px; font-weight: 600; color: {TEXT_PRIMARY}; padding: 0; }}")
        lay.addWidget(self._name_edit, 1)

        self._del_btn = QPushButton("×")
        self._del_btn.setFixedSize(13, 13)
        self._del_btn.setStyleSheet("""
            QPushButton { background: transparent; color: #AEAEB2;
                          border: none; font-size: 11px; font-weight: 600; padding: 0; }
            QPushButton:hover { color: #FF3B30; }
        """)
        self._del_btn.hide()
        self._del_btn.clicked.connect(self._on_del_clicked)
        lay.addWidget(self._del_btn)

        self._set_base_style()

    # ── Public API ────────────────────────────────────────────

    def set_active(self, active: bool):
        self._active = active
        self._set_base_style()

    def set_progress(self, pct: float):
        """pct: 0.0 – 1.0"""
        self._progress = max(0.0, min(1.0, pct))
        self.update()

    def set_progress_color(self, color: str):
        """动态切换进度条颜色（SUCCESS 绿色 / KLING_WAITING 黄色）。"""
        self._progress_color = color
        self.update()

    def get_name(self) -> str:
        return self._name_edit.text()

    # ── Style helpers ─────────────────────────────────────────

    def _set_base_style(self):
        # Active = gray (selected), Inactive = white (unselected)
        bg = CARD_BG if self._active else "white"
        self.setStyleSheet(f"QFrame {{ background: {bg}; border-radius: 8px; border: none; }}")

    # ── Name editing ──────────────────────────────────────────

    def _start_edit(self):
        if self._editing:
            return
        self._editing = True
        self._name_edit.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        self._name_edit.setReadOnly(False)
        self._name_edit.setStyleSheet(
            "QLineEdit { border: 1px solid #007AFF; border-radius: 3px;"
            " background: white; font-size: 10px; font-weight: 600;"
            f" color: {TEXT_PRIMARY}; padding: 1px; }}")
        self._name_edit.selectAll()
        self._name_edit.setFocus()
        self._name_edit.returnPressed.connect(self._finish_edit)
        self._name_edit.editingFinished.connect(self._finish_edit)

    def _finish_edit(self):
        if not self._editing:
            return
        self._editing = False
        try: self._name_edit.returnPressed.disconnect(self._finish_edit)
        except Exception: pass
        try: self._name_edit.editingFinished.disconnect(self._finish_edit)
        except Exception: pass
        self._name_edit.setReadOnly(True)
        self._name_edit.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._name_edit.setStyleSheet(
            "QLineEdit { border: none; background: transparent;"
            f" font-size: 10px; font-weight: 600; color: {TEXT_PRIMARY}; padding: 0; }}")
        self.name_changed.emit(self._name_edit.text())
        self.update()

    # ── Events ────────────────────────────────────────────────

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._start_edit()

    def _on_del_clicked(self):
        """× button only deletes when Cmd (Mac) or Ctrl (Win) is held."""
        from PySide6.QtWidgets import QApplication
        mods = QApplication.queryKeyboardModifiers()
        if mods & (Qt.MetaModifier | Qt.ControlModifier):
            self.delete_requested.emit()

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._start_edit()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and not self._editing:
            self.clicked.emit()

    def enterEvent(self, e):
        self._del_btn.show()
        self.setToolTip("双击重命名 · ⌘+点击× 删除")

    def leaveEvent(self, e):
        if not self._editing:
            self._del_btn.hide()

    # ── Paint: green charge fill behind the name ──────────────

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        rect = QRectF(0, 0, w, h)

        # Active = gray, Inactive = white  (same as _set_base_style)
        bg = QColor(CARD_BG) if self._active else QColor("white")
        p.setPen(Qt.NoPen)
        p.setBrush(bg)
        path = QPainterPath()
        path.addRoundedRect(rect, 8, 8)
        p.drawPath(path)

        # Progress fill (clipped to rounded rect, color reflects Kling status)
        if self._progress > 0:
            fill_w = w * self._progress
            fill_color = QColor(self._progress_color)
            fill_color.setAlpha(55)
            p.setClipPath(path)
            p.setBrush(fill_color)
            p.drawRect(QRectF(0, 0, fill_w, h))
            p.setClipping(False)

        p.end()
        super().paintEvent(event)



# ── TaskSidebar: vertical tab strip on the left ──────────────
class TaskSidebar(QWidget):
    task_switched = Signal(int)
    task_deleted  = Signal(int)
    task_created  = Signal()

    WIDTH = 88

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(self.WIDTH)
        self.setStyleSheet("background: transparent;")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Scrollable area for tabs
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        self._container = QWidget()
        self._container.setStyleSheet("background: transparent;")
        self._tabs_lay = QVBoxLayout(self._container)
        self._tabs_lay.setContentsMargins(0, 0, 0, 0)
        self._tabs_lay.setSpacing(4)
        self._tabs_lay.setAlignment(Qt.AlignTop)
        scroll.setWidget(self._container)
        outer.addWidget(scroll, 1)

        # New task button at bottom
        self._new_btn = QPushButton("+")
        self._new_btn.setFixedSize(self.WIDTH - 4, 34)
        self._new_btn.setStyleSheet(f"""
            QPushButton {{ background: {CARD_BG}; color: {TEXT_MUTED};
                           border: 1.5px dashed {BORDER}; border-radius: 8px;
                           font-size: 18px; margin-top: 4px; }}
            QPushButton:hover {{ background: #E8E8ED; color: {TEXT_PRIMARY}; }}
            QPushButton:disabled {{ opacity: 0.3; }}
        """)
        self._new_btn.clicked.connect(self.task_created)
        outer.addWidget(self._new_btn)

        self._tabs: list[TaskTab] = []
        self._current = -1

    # same API as TaskTabBar
    def add_tab(self, name: str) -> int:
        idx = len(self._tabs)
        tab = TaskTab(name)
        self._tabs.append(tab)
        self._tabs_lay.addWidget(tab)
        self._rewire(idx)
        self._new_btn.setEnabled(len(self._tabs) < MAX_TASKS)
        return idx

    def remove_tab(self, idx: int):
        if len(self._tabs) <= 1:
            return
        tab = self._tabs.pop(idx)
        self._tabs_lay.removeWidget(tab)
        tab.deleteLater()
        for i in range(len(self._tabs)):
            self._rewire(i)
        self._new_btn.setEnabled(True)
        if self._current >= len(self._tabs):
            self._current = len(self._tabs) - 1

    def set_current(self, idx: int):
        if 0 <= self._current < len(self._tabs):
            self._tabs[self._current].set_active(False)
        self._current = idx
        if 0 <= idx < len(self._tabs):
            self._tabs[idx].set_active(True)

    def set_step_state(self, task_idx: int, step: str, state: str):
        pass  # replaced by set_progress

    def set_progress(self, task_idx: int, pct: float):
        if 0 <= task_idx < len(self._tabs):
            self._tabs[task_idx].set_progress(pct)

    def set_progress_color(self, task_idx: int, color: str):
        if 0 <= task_idx < len(self._tabs):
            self._tabs[task_idx].set_progress_color(color)

    def reset_tab_dots(self, task_idx: int):
        if 0 <= task_idx < len(self._tabs):
            self._tabs[task_idx].set_progress(0.01)
            self._tabs[task_idx].set_progress_color(SUCCESS)  # 重置为绿色

    def count(self) -> int:
        return len(self._tabs)

    def _rewire(self, i: int):
        tab = self._tabs[i]
        try: tab.clicked.disconnect()
        except Exception: pass
        try: tab.delete_requested.disconnect()
        except Exception: pass
        tab.clicked.connect(lambda ii=i: self.task_switched.emit(ii))
        tab.delete_requested.connect(lambda ii=i: self.task_deleted.emit(ii))


# ── Stylesheet ───────────────────────────────────────────────
def _app_stylesheet() -> str:
    return f"""
QWidget {{
    background: {BG};
    color: {TEXT_PRIMARY};
    font-size: 14px;
}}
QTextEdit {{
    background: {BG};
    border: 1px solid {BORDER};
    border-radius: 10px;
    padding: 8px;
    font-size: 13px;
    color: {TEXT_PRIMARY};
    selection-background-color: {BORDER};
}}
QTextEdit:focus {{
    border: 1.5px solid {FOCUS_BORDER};
    outline: none;
}}
QScrollBar:vertical {{
    background: transparent;
    width: 6px;
    margin: 0;
    border: none;
}}
QScrollBar::handle:vertical {{
    background: {BORDER};
    border-radius: 3px;
    min-height: 20px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}
QPushButton#primary {{
    background: {BTN_PRIMARY};
    color: white;
    border: none;
    border-radius: 10px;
    padding: 10px 28px;
    font-size: 15px;
    font-weight: 600;
}}
QPushButton#primary:hover   {{ background: {BTN_PRIMARY_HVR}; }}
QPushButton#primary:disabled {{ background: {BORDER}; color: {TEXT_MUTED}; }}
QPushButton#secondary {{
    background: {CARD_BG};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER};
    border-radius: 10px;
    padding: 10px 24px;
    font-size: 15px;
    font-weight: 500;
}}
QPushButton#secondary:hover    {{ background: #E8E8ED; }}
QPushButton#secondary:disabled {{ color: {TEXT_MUTED}; }}
QProgressBar {{
    background: {BORDER};
    border: none;
    border-radius: 4px;
    height: 6px;
}}
QProgressBar::chunk {{
    background: {ACCENT};
    border-radius: 4px;
}}
QScrollArea {{ border: none; background: transparent; }}
"""


# ── ImageThumbnail: single uploaded image with × button ──────
class ImageThumbnail(QFrame):
    """Fixed-size thumbnail with a delete button overlay."""

    def __init__(self, path: str, on_delete, parent=None):
        super().__init__(parent)
        self.path = path
        self.setFixedSize(CELL_W, CELL_H)
        self.setStyleSheet(f"QFrame {{ background: {CARD_BG}; border-radius: 10px; }}")

        # Thumbnail image
        img_lbl = QLabel(self)
        img_lbl.setFixedSize(CELL_W, CELL_H)
        img_lbl.setAlignment(Qt.AlignCenter)
        pix = QPixmap(path)
        if not pix.isNull():
            scaled = pix.scaled(QSize(CELL_W, CELL_H),
                                Qt.KeepAspectRatioByExpanding,
                                Qt.SmoothTransformation)
            x = (scaled.width()  - CELL_W) // 2
            y = (scaled.height() - CELL_H) // 2
            img_lbl.setPixmap(scaled.copy(x, y, CELL_W, CELL_H))

        # × delete button (top-right)
        del_btn = QPushButton("×", self)
        del_btn.setFixedSize(20, 20)
        del_btn.move(CELL_W - 24, 4)
        del_btn.setStyleSheet("""
            QPushButton {
                background: rgba(0,0,0,0.55);
                color: white;
                border: none;
                border-radius: 10px;
                font-size: 13px;
                font-weight: 700;
                padding: 0;
            }
            QPushButton:hover { background: rgba(255,59,48,0.85); }
        """)
        del_btn.clicked.connect(on_delete)
        del_btn.raise_()


# ── AddCell: the "＋" placeholder ─────────────────────────────
class AddCell(QLabel):
    """Clickable dashed-border add cell."""

    def __init__(self, on_click, parent=None):
        super().__init__("＋", parent)
        self._on_click = on_click
        self.setFixedSize(CELL_W, CELL_H)
        self.setAlignment(Qt.AlignCenter)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(f"""
            QLabel {{
                background: {CARD_BG};
                border: 1.5px dashed {BORDER};
                border-radius: 10px;
                color: {TEXT_MUTED};
                font-size: 22px;
            }}
        """)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._on_click()


# ── ImageGrid ─────────────────────────────────────────────────
class ImageGrid(QWidget):
    """Dynamic 2-column image grid.

    Supports:
    - Click "＋" to open multi-file dialog
    - Drag & drop (multiple files)
    - Cmd/Ctrl+V to paste from clipboard
    - Per-image × delete button
    - Max MAX_IMAGES images
    """

    COLS = 2

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self._paths: list[str] = []
        self._tmp_files: list[str] = []   # clipboard temps to clean up

        self._grid = QGridLayout(self)
        self._grid.setSpacing(8)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._refresh()

    # ── Public ───────────────────────────────────────────────

    def get_paths(self) -> list[str]:
        return list(self._paths)

    def add_paths(self, paths: list[str]):
        """Add images, up to MAX_IMAGES total."""
        for p in paths:
            if len(self._paths) >= MAX_IMAGES:
                break
            if p not in self._paths:
                self._paths.append(p)
        self._refresh()

    # ── Internal refresh ─────────────────────────────────────

    def _refresh(self):
        # Remove all widgets from grid
        while self._grid.count():
            item = self._grid.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        pos = 0
        for i, path in enumerate(self._paths):
            idx = i  # capture for lambda
            thumb = ImageThumbnail(path, on_delete=lambda _, j=idx: self._delete(j))
            self._grid.addWidget(thumb, pos // self.COLS, pos % self.COLS)
            pos += 1

        if len(self._paths) < MAX_IMAGES:
            add = AddCell(on_click=self._open_dialog)
            self._grid.addWidget(add, pos // self.COLS, pos % self.COLS)

    def _delete(self, index: int):
        if 0 <= index < len(self._paths):
            self._paths.pop(index)
            self._refresh()

    def _open_dialog(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择图片（可多选）", "",
            "Images (*.jpg *.jpeg *.png *.webp)"
        )
        if paths:
            self.add_paths(paths)

    # ── Drag & drop ──────────────────────────────────────────

    def dragEnterEvent(self, e: QDragEnterEvent):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e: QDropEvent):
        exts = {".jpg", ".jpeg", ".png", ".webp"}
        paths = [
            u.toLocalFile() for u in e.mimeData().urls()
            if Path(u.toLocalFile()).suffix.lower() in exts
        ]
        self.add_paths(paths)

    # ── Clipboard paste ───────────────────────────────────────

    def keyPressEvent(self, e):
        is_paste = (
            e.matches(QKeySequence.Paste) or
            (e.key() == Qt.Key_V and e.modifiers() & Qt.ControlModifier) or
            (e.key() == Qt.Key_V and e.modifiers() & Qt.MetaModifier)
        )
        if is_paste:
            self._paste_from_clipboard()
        else:
            super().keyPressEvent(e)

    def _paste_from_clipboard(self):
        cb = QApplication.clipboard()
        image: QImage = cb.image()
        if image.isNull():
            # Maybe the clipboard has a file URL
            mime = cb.mimeData()
            if mime.hasUrls():
                paths = [u.toLocalFile() for u in mime.urls()
                         if Path(u.toLocalFile()).suffix.lower()
                         in {".jpg", ".jpeg", ".png", ".webp"}]
                self.add_paths(paths)
            return

        # Save clipboard image to a temp file
        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        tmp.close()
        image.save(tmp.name)
        self._tmp_files.append(tmp.name)
        self.add_paths([tmp.name])

    def focusInEvent(self, e):
        # Accept keyboard events when clicked
        super().focusInEvent(e)


# ── Plain-text-only QTextEdit ────────────────────────────────
class _PlainTextEdit(QTextEdit):
    """QTextEdit that always pastes as plain text, stripping any rich formatting."""

    def insertFromMimeData(self, source):
        self.insertPlainText(source.text())


# ── Left panel ────────────────────────────────────────────────
class LeftPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        # ── Image card ──
        img_card = self._make_card()
        img_card_layout = QVBoxLayout(img_card)
        img_card_layout.setContentsMargins(14, 14, 14, 14)
        img_card_layout.setSpacing(8)

        lbl = QLabel("产品图片")
        lbl.setStyleSheet(f"font-weight: 600; font-size: 13px; color: {TEXT_MUTED};")
        img_card_layout.addWidget(lbl)

        self.image_grid = ImageGrid()
        img_card_layout.addWidget(self.image_grid)
        layout.addWidget(img_card)

        # ── Sellpoint text ──
        self._txt_card = self._make_card(border=True)
        txt_layout = QVBoxLayout(self._txt_card)
        txt_layout.setContentsMargins(14, 14, 14, 14)
        txt_layout.setSpacing(8)

        lbl2 = QLabel("卖点文案")
        lbl2.setStyleSheet(f"font-weight: 600; font-size: 13px; color: {TEXT_MUTED};")
        txt_layout.addWidget(lbl2)
        txt_card = self._txt_card

        self.text_edit = _PlainTextEdit()
        self.text_edit.setMinimumHeight(120)
        self.text_edit.setFrameShape(QFrame.NoFrame)
        self.text_edit.setFrameShadow(QFrame.Plain)
        txt_layout.addWidget(self.text_edit)
        layout.addWidget(txt_card, 1)

    @staticmethod
    def _make_card(border: bool = False) -> QFrame:
        card = QFrame()
        card.setStyleSheet(f"QFrame {{ background: {CARD_BG}; border-radius: 14px; }}")
        return card

    def get_image_paths(self) -> list[str]:
        return self.image_grid.get_paths()

    def get_sellpoint(self) -> str:
        return self.text_edit.toPlainText().strip()

    def snapshot(self) -> tuple[list[str], str]:
        """Return (image_paths, sellpoint_text)."""
        return self.image_grid.get_paths(), self.text_edit.toPlainText()

    def restore(self, image_paths: list[str], sellpoint: str):
        """Restore input fields from saved state."""
        self.image_grid._paths = list(image_paths)
        self.image_grid._refresh()
        self.text_edit.setPlainText(sellpoint)


# ── IndicatorNode: shape-coded for color-blind accessibility ─
class IndicatorNode(QWidget):
    S = 18   # widget size

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(self.S, self.S)
        self._state = "pending"
        self._pulse = 0.0
        self._pulse_dir = 1
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    def set_state(self, state: str):
        self._state = state
        if state == "active":
            self._timer.start(25)
        else:
            self._timer.stop()
            self._pulse = 0.0
        self.update()

    def _tick(self):
        self._pulse += 0.04 * self._pulse_dir
        if self._pulse >= 1.0:   self._pulse_dir = -1
        elif self._pulse <= 0.0: self._pulse_dir =  1
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        s = self.S
        cx, cy, r = s / 2, s / 2, s / 2 - 2

        if self._state == "pending":
            # ○ hollow ring
            p.setPen(QPen(QColor(BORDER), 1.5))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))

        elif self._state == "active":
            # pulsing outer ring
            pr = r + 1 + self._pulse * 3
            pc = QColor(ACCENT)
            pc.setAlpha(int(100 * (1 - self._pulse)))
            p.setPen(QPen(pc, 1.2))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(QRectF(cx - pr, cy - pr, pr * 2, pr * 2))
            # filled inner dot
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(ACCENT))
            ir = r * 0.62
            p.drawEllipse(QRectF(cx - ir, cy - ir, ir * 2, ir * 2))

        elif self._state in ("done", "skipped"):
            color = QColor(SUCCESS) if self._state == "done" else QColor(BORDER)
            p.setPen(Qt.NoPen)
            p.setBrush(color)
            p.drawEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))
            # ✓ checkmark
            p.setPen(QPen(QColor("white"), 1.5, Qt.SolidLine,
                          Qt.RoundCap, Qt.RoundJoin))
            path = QPainterPath()
            path.moveTo(cx - r * .38, cy + r * .05)
            path.lineTo(cx - r * .08, cy + r * .42)
            path.lineTo(cx + r * .48, cy - r * .38)
            p.drawPath(path)

        elif self._state == "failed":
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(ERROR_COLOR))
            p.drawEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))
            # ✕
            p.setPen(QPen(QColor("white"), 1.5, Qt.SolidLine, Qt.RoundCap))
            off = r * .33
            p.drawLine(QPointF(cx - off, cy - off), QPointF(cx + off, cy + off))
            p.drawLine(QPointF(cx + off, cy - off), QPointF(cx - off, cy + off))

        p.end()


# ── StepCard: semi-transparent card per step ─────────────────
class StepCard(QFrame):
    H_IDLE   = 40
    H_ACTIVE = 52

    def __init__(self, key: str, name: str, parent=None):
        super().__init__(parent)
        self._state = "pending"
        self._shimmer = 0.0
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedHeight(self.H_ACTIVE)   # constant — no layout shift on state change
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 0, 16, 0)
        lay.setSpacing(9)
        lay.setAlignment(Qt.AlignCenter)

        self._node = IndicatorNode()
        lay.addWidget(self._node)

        self._lbl = QLabel(name)
        self._lbl.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        self._lbl.setStyleSheet(
            f"font-size: 12px; color: {TEXT_MUTED}; background: transparent;")
        lay.addWidget(self._lbl)

        self._anim = QVariantAnimation(self)
        self._anim.setDuration(1500)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setLoopCount(-1)
        self._anim.setEasingCurve(QEasingCurve.Linear)
        self._anim.valueChanged.connect(self._on_shimmer)

    def _on_shimmer(self, v: float):
        self._shimmer = v
        self.update()

    def set_state(self, state: str):
        self._state = state
        self._node.set_state(state)

        if state == "active":
            self._lbl.setStyleSheet(
                f"font-size: 13px; font-weight: 600;"
                f" color: {TEXT_PRIMARY}; background: transparent;")
            sh = QGraphicsDropShadowEffect(self)
            sh.setBlurRadius(18)
            sh.setColor(QColor(0, 0, 0, 35))
            sh.setOffset(0, 4)
            self.setGraphicsEffect(sh)
            self._anim.start()
        else:
            self._anim.stop()
            self._shimmer = 0.0
            self.setGraphicsEffect(None)
            color = ERROR_COLOR if state == "failed" else TEXT_MUTED
            weight = "500" if state in ("done", "skipped") else "400"
            self._lbl.setStyleSheet(
                f"font-size: 12px; font-weight: {weight};"
                f" color: {color}; background: transparent;")
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(1, 1, self.width() - 2, self.height() - 2)

        if self._state == "active":
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, 200))
            p.drawRoundedRect(rect, 10, 10)
            # shimmer sweep
            if self._shimmer > 0:
                sx = self.width() * (self._shimmer * 1.5 - 0.25)
                g = QLinearGradient(sx - 70, 0, sx + 70, self.height())
                g.setColorAt(0,   QColor(255, 255, 255, 0))
                g.setColorAt(0.5, QColor(255, 255, 255, 50))
                g.setColorAt(1,   QColor(255, 255, 255, 0))
                clip = QPainterPath()
                clip.addRoundedRect(rect, 10, 10)
                p.setClipPath(clip)
                p.setBrush(QBrush(g))
                p.drawRoundedRect(rect, 10, 10)
                p.setClipping(False)

        elif self._state in ("done", "skipped"):
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, 160))
            p.drawRoundedRect(rect, 10, 10)

        else:  # pending / failed
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, 90))
            p.drawRoundedRect(rect, 10, 10)

        p.end()
        super().paintEvent(event)


# ── StepList: vertical stack of StepCards ────────────────────
class StepList(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedWidth(220)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(5)
        self._cards: dict[str, StepCard] = {}
        for key, name in STEP_LABELS.items():
            card = StepCard(key, name)
            self._cards[key] = card
            lay.addWidget(card)

    def reset(self):
        for key in STEP_ORDER:
            self._cards[key].set_state("pending")

    def set_active(self, step: str):  self._cards[step].set_state("active")
    def set_done(self, step: str):    self._cards[step].set_state("done")
    def set_error(self, step: str):   self._cards[step].set_state("failed")
    def set_skipped(self, step: str): self._cards[step].set_state("skipped")


# ── AspectRatioContainer: keeps 16:9 or 9:16 ─────────────────
class AspectContainer(QWidget):
    """Wraps a child widget and enforces an aspect ratio by adjusting height."""

    def __init__(self, child: QWidget, w_ratio=16, h_ratio=9, parent=None):
        super().__init__(parent)
        self._w = w_ratio
        self._h = h_ratio
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(child)
        self._child = child

    def set_ratio(self, w: int, h: int):
        self._w, self._h = w, h
        self.updateGeometry()

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return int(width * self._h / self._w)

    def sizeHint(self):
        w = self.width() or 480
        return QSize(w, self.heightForWidth(w))


# ── Right panel ───────────────────────────────────────────────
class RightPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        card = QFrame()
        card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        card.setStyleSheet(f"QFrame {{ background: {CARD_BG}; border-radius: 14px; }}")
        outer.addWidget(card)

        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(24, 24, 24, 24)
        card_layout.setSpacing(16)

        # Stack: [0] idle  [1] running  [2] done
        self.stack = QStackedWidget()
        card_layout.addWidget(self.stack)

        # ── Page 0: idle — blank 16:9 placeholder ──
        idle_w = QWidget()
        idle_w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.stack.addWidget(idle_w)

        # ── Page 1: running ──
        run_w = QWidget()
        run_w.setAttribute(Qt.WA_TranslucentBackground)
        run_w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        run_l = QVBoxLayout(run_w)
        run_l.setContentsMargins(0, 0, 0, 0)
        run_l.setSpacing(0)

        run_l.addStretch(1)
        self.step_list = StepList()
        run_l.addWidget(self.step_list, 0, Qt.AlignHCenter)
        run_l.addStretch(1)

        log_scroll = QScrollArea()
        log_scroll.setWidgetResizable(True)
        log_scroll.setStyleSheet("background: transparent; border: none;")
        self._log_container = QWidget()
        self._log_container.setStyleSheet("background: transparent;")
        self._log_layout = QVBoxLayout(self._log_container)
        self._log_layout.setAlignment(Qt.AlignTop)
        self._log_layout.setSpacing(2)
        self._log_layout.setContentsMargins(0, 0, 0, 0)
        log_scroll.setWidget(self._log_container)
        self._log_scroll = log_scroll
        run_l.addWidget(log_scroll, 1)

        self.stack.addWidget(run_w)

        # ── Page 2: done ──
        done_w = QWidget()
        done_w.setStyleSheet("background: transparent;")
        done_l = QVBoxLayout(done_w)
        done_l.setContentsMargins(0, 0, 0, 0)
        done_l.setSpacing(0)

        # Video container with overlay play button
        video_container = QWidget()
        video_container.setStyleSheet("background: transparent;")
        video_container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        vc_layout = QVBoxLayout(video_container)
        vc_layout.setContentsMargins(0, 0, 0, 0)

        self.video_widget = QVideoWidget(video_container)
        self.video_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.video_widget.setStyleSheet("background: #000; border-radius: 10px;")

        self._aspect_container = AspectContainer(self.video_widget, 16, 9)
        self._aspect_container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        vc_layout.addWidget(self._aspect_container, 1)

        # Overlay play button — centered, semi-transparent, icon only
        self.play_btn = QPushButton("▶", video_container)
        self.play_btn.setFixedSize(64, 64)
        self.play_btn.setStyleSheet("""
            QPushButton {
                background: rgba(0,0,0,0.45);
                color: white;
                border: none;
                border-radius: 32px;
                font-size: 22px;
                padding-left: 4px;
            }
            QPushButton:hover { background: rgba(0,0,0,0.65); }
        """)
        self.play_btn.hide()
        self.play_btn.clicked.connect(self._toggle_play)

        done_l.addWidget(video_container, 1)

        self.player = QMediaPlayer()
        # Attach audio output for sound
        from PySide6.QtMultimedia import QAudioOutput
        self._audio_output = QAudioOutput()
        self._audio_output.setVolume(1.0)
        self.player.setAudioOutput(self._audio_output)
        self.player.setVideoOutput(self.video_widget)
        self.player.hasVideoChanged.connect(self._on_has_video)
        self.player.playbackStateChanged.connect(self._on_playback_state)

        # Install event filter on video_container to catch hover
        video_container.setMouseTracking(True)
        video_container.installEventFilter(self)
        self._video_container = video_container

        self.stack.addWidget(done_w)

        self._completed_steps = 0
        self._step_states: dict[str, str] = {}
        self._log_lines: list[str] = []
        self._mp4_path = ""
        self._output_dir = ""

    # ── Public ───────────────────────────────────────────────

    def show_idle(self):
        self.stack.setCurrentIndex(0)

    def snapshot(self) -> dict:
        """Save current panel state to a plain dict."""
        return {
            "panel_state": self.stack.currentIndex(),
            "step_states": dict(self._step_states),
            "log_lines": list(self._log_lines),
            "mp4_path": self._mp4_path,
            "output_dir": self._output_dir,
        }

    def restore(self, state: dict):
        """Restore panel state from a dict saved by snapshot()."""
        self._step_states = dict(state.get("step_states", {}))
        self._mp4_path = state.get("mp4_path", "")
        self._output_dir = state.get("output_dir", "")

        # Restore log
        self._log_lines = list(state.get("log_lines", []))
        while self._log_layout.count():
            item = self._log_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for line in self._log_lines:
            self._add_log_label(line)

        ps = state.get("panel_state", 0)
        if ps == 0:
            self.show_idle()
        elif ps == 1:
            self.step_list.reset()
            self._completed_steps = 0
            for step, st in self._step_states.items():
                if st == "active":      self.step_list.set_active(step)
                elif st == "completed": self.step_list.set_done(step)
                elif st == "failed":    self.step_list.set_error(step)
                elif st == "skipped":   self.step_list.set_skipped(step)
            self.stack.setCurrentIndex(1)
        elif ps == 2:
            self.show_done(self._mp4_path)

    def _add_log_label(self, message: str):
        lbl = QLabel(message)
        lbl.setWordWrap(True)
        lbl.setStyleSheet(
            f"font-size: 11px; color: {TEXT_MUTED};"
            f" background: transparent; font-family: monospace;")
        self._log_layout.addWidget(lbl)

    def show_running(self):
        self.step_list.reset()
        self._completed_steps = 0
        self._step_states = {}
        self._log_lines = []
        self._mp4_path = ""
        while self._log_layout.count():
            item = self._log_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.stack.setCurrentIndex(1)

    def on_step_progress(self, step: str, status: str, detail: str):
        # Normalise "started" → "active" for consistent internal storage
        state = "active" if status == "started" else status
        self._step_states[step] = state
        if state == "active":
            self.step_list.set_active(step)
        elif state == "completed":
            self.step_list.set_done(step)
            self._completed_steps += 1
        elif state == "skipped":
            self.step_list.set_skipped(step)
            self._completed_steps += 1
        elif state == "failed":
            self.step_list.set_error(step)

    def append_log(self, message: str):
        self._log_lines.append(message)
        self._add_log_label(message)
        self._log_scroll.verticalScrollBar().setValue(
            self._log_scroll.verticalScrollBar().maximum())

    def show_done(self, mp4_path: str):
        self._mp4_path = mp4_path
        if mp4_path and Path(mp4_path).exists():
            self.player.setSource(QUrl.fromLocalFile(mp4_path))
        self.stack.setCurrentIndex(2)
        # 延迟 150ms 等布局稳定后自动播放并定位播放按钮
        QTimer.singleShot(150, self._start_playback)

    def _start_playback(self):
        self._reposition_play_btn()
        if self._mp4_path and Path(self._mp4_path).exists():
            self.player.play()
        else:
            # 文件不存在时显示播放按钮作为提示
            self.play_btn.show()
            self.play_btn.raise_()

    def show_error(self, message: str):
        self.append_log(f"❌ {message}")

    def _reposition_play_btn(self):
        vc = self._video_container
        btn = self.play_btn
        btn.move((vc.width() - btn.width()) // 2,
                 (vc.height() - btn.height()) // 2)

    def eventFilter(self, obj, event):
        from PySide6.QtCore import QEvent
        if obj is self._video_container:
            if event.type() == QEvent.Enter:
                if self.player.playbackState() != QMediaPlayer.PlayingState:
                    self.play_btn.show()
                    self.play_btn.raise_()
            elif event.type() == QEvent.Leave:
                self.play_btn.hide()
            elif event.type() == QEvent.Resize:
                self._reposition_play_btn()
        return super().eventFilter(obj, event)

    def _on_playback_state(self, state):
        if state == QMediaPlayer.PlayingState:
            self.play_btn.hide()

    # ── Private ───────────────────────────────────────────────

    def _toggle_play(self):
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            self.player.play()
            self.play_btn.hide()

    def _on_has_video(self, has: bool):
        """Adjust aspect ratio container once video dimensions are known."""
        if not has:
            return
        size = self.player.videoSink().videoSize() if hasattr(self.player, "videoSink") else None
        if size and size.isValid() and size.width() > 0:
            self._aspect_container.set_ratio(size.width(), size.height())
            self._aspect_container.updateGeometry()


# ── Main Window ───────────────────────────────────────────────
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Product Video Pipeline  v0.1")
        self.setMinimumSize(960, 640)

        # Multi-task state
        self._tasks: list[TaskState] = []
        self._current_idx: int = 0
        self._result: dict = {}

        # 全局任务执行队列（pipeline 级并发控制）
        from config.settings import APP_MAX_RUNNING_TASKS
        self._task_queue = AppTaskQueue(APP_MAX_RUNNING_TASKS)

        self._setup_ui()

        # Init first task
        self._create_task()

        # Copyright overlay
        self._copyright = QLabel("亚声威格 © AI创新 2026", self.centralWidget())
        self._copyright.setStyleSheet(f"color: {BORDER}; font-size: 11px;")
        self._copyright.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._copyright.adjustSize()
        self._copyright.raise_()

    # ── Task management ──────────────────────────────────────

    def _create_task(self):
        idx = len(self._tasks)
        name = f"任务 {idx + 1}"
        task = TaskState(name=name)
        self._tasks.append(task)
        tab_idx = self.task_sidebar.add_tab(name)
        self._switch_task(tab_idx, save_current=False)

    def _switch_task(self, new_idx: int, save_current: bool = True):
        if save_current and 0 <= self._current_idx < len(self._tasks):
            self._save_current()

        self._current_idx = new_idx
        task = self._tasks[new_idx]

        # Restore left panel
        self.left_panel.restore(task.image_paths, task.sellpoint)

        # Restore right panel
        self.right_panel.restore({
            "panel_state": task.panel_state,
            "step_states": task.step_states,
            "log_lines":   task.log_lines,
            "mp4_path":    task.mp4_path,
            "output_dir":  task.output_dir,
        })

        self.task_sidebar.set_current(new_idx)

        # Button states
        running = task.worker is not None and task.worker.isRunning()
        self.start_btn.setEnabled(not running)
        self.stop_btn.setEnabled(running)
        self.files_btn.setEnabled(bool(task.output_dir))
        self._result = {"output_dir": task.output_dir, "mp4": task.mp4_path}

        # Show/hide input panel based on task state
        if task.panel_state == 0:
            self._expand_left_panel()
        else:
            self._collapse_left_panel()

    # ── Left panel collapse / expand ────────────────────────

    _STEP_PCT: dict = {}   # built lazily

    @classmethod
    def _step_to_pct(cls, step: str, status: str) -> float:
        if not cls._STEP_PCT:
            n = len(STEP_ORDER)
            for i, k in enumerate(STEP_ORDER):
                cls._STEP_PCT[(k, "started")]   = 0.01 + (i / n) * 0.94
                cls._STEP_PCT[(k, "completed")] = 0.01 + ((i + 1) / n) * 0.94
                cls._STEP_PCT[(k, "skipped")]   = 0.01 + ((i + 1) / n) * 0.94
                cls._STEP_PCT[(k, "failed")]    = 0.01 + (i / n) * 0.94
        return cls._STEP_PCT.get((step, status), 0.0)

    def _fold_then_new_task(self):
        """Collapse current input panel, then open a fresh task."""
        def _after_fold():
            self._create_task()          # creates + switches to new task
            self._expand_left_panel()    # reveal new blank input panel

        self._collapse_left_panel(callback=_after_fold)

    def _collapse_left_panel(self, callback=None):
        if not self.left_panel.isVisible():
            if callback:
                callback()
            return
        anim = QPropertyAnimation(self.left_panel, b"maximumWidth", self)
        anim.setDuration(220)
        anim.setStartValue(self.left_panel.width())
        anim.setEndValue(0)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        def _done():
            self.left_panel.hide()
            if callback:
                callback()
        anim.finished.connect(_done)
        anim.start()
        self._panel_anim = anim

    def _expand_left_panel(self):
        self.left_panel.setMaximumWidth(0)
        self.left_panel.show()
        anim = QPropertyAnimation(self.left_panel, b"maximumWidth", self)
        anim.setDuration(220)
        anim.setStartValue(0)
        anim.setEndValue(272)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.finished.connect(lambda: self.left_panel.setMaximumWidth(16777215))
        anim.start()
        self._panel_anim = anim

    def _save_current(self):
        if not (0 <= self._current_idx < len(self._tasks)):
            return
        task = self._tasks[self._current_idx]
        task.image_paths, task.sellpoint = self.left_panel.snapshot()
        snap = self.right_panel.snapshot()
        task.panel_state = snap["panel_state"]
        task.step_states = snap["step_states"]
        task.log_lines   = snap["log_lines"]
        task.mp4_path    = snap["mp4_path"]
        task.output_dir  = snap.get("output_dir", task.output_dir)

    def _confirm_delete_task(self, idx: int):
        from PySide6.QtWidgets import QMessageBox
        if not (0 <= idx < len(self._tasks)):
            return
        name = self._tasks[idx].name
        reply = QMessageBox.question(
            self, "删除任务",
            f"确定删除「{name}」？此操作无法撤销。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self._delete_task(idx)

    def _delete_task(self, idx: int):
        if len(self._tasks) <= 1:
            return
        # Stop worker if running
        task = self._tasks[idx]
        if task.worker and task.worker.isRunning():
            task.worker.stop()
            task.worker.wait(2000)
        self._tasks.pop(idx)
        self.task_sidebar.remove_tab(idx)
        new_idx = min(idx, len(self._tasks) - 1)
        self._switch_task(new_idx, save_current=False)

    # ── Worker progress routing ──────────────────────────────

    def _on_task_progress(self, task_idx: int, step: str, status: str, detail: str):
        if not (0 <= task_idx < len(self._tasks)):
            return

        # Kling 排队状态 → 只改颜色，不推进百分比
        if status == "kling_queued":
            self.task_sidebar.set_progress_color(task_idx, KLING_WAITING)
            return
        if status == "kling_active":
            self.task_sidebar.set_progress_color(task_idx, SUCCESS)
            return

        self._tasks[task_idx].step_states[step] = "active" if status == "started" else status
        # Update tab progress bar
        pct = self._step_to_pct(step, status)
        self.task_sidebar.set_progress(task_idx, pct)
        # 步骤完成后确保颜色恢复绿色（防止之前黄色残留）
        if status == "completed":
            self.task_sidebar.set_progress_color(task_idx, SUCCESS)
        # Update right panel only if this task is currently shown
        if task_idx == self._current_idx:
            self.right_panel.on_step_progress(step, status, detail)

    def _on_task_log(self, task_idx: int, message: str):
        if not (0 <= task_idx < len(self._tasks)):
            return
        self._tasks[task_idx].log_lines.append(message)
        if task_idx == self._current_idx:
            self.right_panel.append_log(message)

    def _on_task_run_dirs(self, task_idx: int, output_dir: str):
        if not (0 <= task_idx < len(self._tasks)):
            return
        self._tasks[task_idx].output_dir = output_dir
        if task_idx == self._current_idx:
            self._result["output_dir"] = output_dir
            self.files_btn.setEnabled(True)

    def _on_task_finished(self, task_idx: int, result: dict):
        if not (0 <= task_idx < len(self._tasks)):
            return
        task = self._tasks[task_idx]
        # task.worker cleared by QThread.finished → avoids GC while thread runs
        task.mp4_path   = result.get("mp4", "")
        task.output_dir = result.get("output_dir", task.output_dir)
        if result.get("aborted"):
            task.panel_state = 1
        else:
            task.panel_state = 2
            self.task_sidebar.set_progress(task_idx, 1.0)   # 100%

        if task_idx == self._current_idx:
            self.start_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
            self.files_btn.setEnabled(bool(task.output_dir))
            self._result = {"output_dir": task.output_dir, "mp4": task.mp4_path}
            if result.get("aborted"):
                self.right_panel.append_log("已停止，进度已保存，下次可继续")
            else:
                self.right_panel.show_done(task.mp4_path)

    def _on_task_error(self, task_idx: int, message: str):
        if not (0 <= task_idx < len(self._tasks)):
            return
        # task.worker cleared by QThread.finished → avoids GC while thread runs
        if task_idx == self._current_idx:
            self.start_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
            self.right_panel.show_error(message)

    def showEvent(self, event):
        super().showEvent(event)
        self._reposition_copyright()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reposition_copyright()

    def _reposition_copyright(self):
        lbl = self._copyright
        lbl.adjustSize()
        cw = self.centralWidget()
        # Horizontal: true center of central widget
        x = (cw.width() - lbl.width()) // 2
        # Vertical: align with start_btn vertical center using reliable mapTo
        from PySide6.QtCore import QPoint
        btn_center = self.start_btn.mapTo(cw, QPoint(
            self.start_btn.width() // 2,
            self.start_btn.height() // 2,
        ))
        y = btn_center.y() - lbl.height() // 2 + 10
        lbl.move(x, y)
        lbl.raise_()

    def _setup_ui(self):
        root = QWidget()
        self.setCentralWidget(root)

        main = QVBoxLayout(root)
        main.setContentsMargins(20, 20, 20, 20)
        main.setSpacing(16)

        # ── 标题 + 视频模型切换 ─────────────────────────────
        header = QHBoxLayout()
        header.setSpacing(10)
        header.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        title_col = QVBoxLayout()
        title_col.setSpacing(4)
        title_col.setAlignment(Qt.AlignLeft)

        title = QLabel("Product Video Pipeline")
        title.setStyleSheet(f"font-size: 20px; font-weight: 700; color: {TEXT_PRIMARY};")
        title_col.addWidget(title)

        # 视频模型切换按钮（小 chip 风格）
        self._video_model = "kling"
        self._kling_mode  = "std"   # std | pro
        self._model_btn = QPushButton("视频模型：Kling Std  ▾")
        self._model_btn.setStyleSheet(f"""
            QPushButton {{
                font-size: 10px; color: {TEXT_MUTED};
                background: {CARD_BG}; border: 1px solid {BORDER};
                border-radius: 8px; padding: 2px 9px;
            }}
            QPushButton:hover {{ background: #E8E8ED; border-color: {FOCUS_BORDER}; }}
        """)
        self._model_btn.setFixedHeight(20)
        self._model_btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self._model_btn.clicked.connect(self._on_model_select)
        title_col.addWidget(self._model_btn)

        header.addLayout(title_col)
        header.addStretch()
        main.addLayout(header)

        content = QHBoxLayout()
        content.setSpacing(12)

        # Vertical task sidebar
        self.task_sidebar = TaskSidebar()
        content.addWidget(self.task_sidebar)

        self.left_panel = LeftPanel()
        self.left_panel.setFixedWidth(272)
        content.addWidget(self.left_panel)

        self.right_panel = RightPanel()
        content.addWidget(self.right_panel, 1)

        main.addLayout(content, 1)

        bottom = QHBoxLayout()
        bottom.setSpacing(0)
        bottom.setContentsMargins(0, 0, 0, 0)

        # Left button group — offset by sidebar width so buttons center under 卖点文案 box
        bottom.addSpacing(TaskSidebar.WIDTH + 12)   # sidebar(88) + content spacing(12)
        btn_wrapper = QWidget()
        btn_wrapper.setFixedWidth(272)
        btn_inner = QHBoxLayout(btn_wrapper)
        btn_inner.setContentsMargins(0, 0, 0, 0)
        btn_inner.setSpacing(12)
        btn_inner.setAlignment(Qt.AlignCenter)

        self.start_btn = QPushButton("开始")
        self.start_btn.setObjectName("primary")
        self.start_btn.setFixedWidth(100)
        self.start_btn.clicked.connect(self._on_start)
        btn_inner.addWidget(self.start_btn)

        self.stop_btn = QPushButton("停止")
        self.stop_btn.setObjectName("secondary")
        self.stop_btn.setFixedWidth(80)
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._on_stop)
        btn_inner.addWidget(self.stop_btn)

        bottom.addWidget(btn_wrapper)
        bottom.addStretch()

        self.files_btn = QPushButton("视频文件")
        self.files_btn.setObjectName("secondary")
        self.files_btn.setFixedWidth(120)
        self.files_btn.setEnabled(False)
        self.files_btn.clicked.connect(self._on_open_files)
        bottom.addWidget(self.files_btn)

        settings_btn = QPushButton("⚙")
        settings_btn.setObjectName("secondary")
        settings_btn.setFixedWidth(32)
        settings_btn.setToolTip("并发设置（管理员）")
        settings_btn.clicked.connect(self._on_open_settings)
        from config.settings import ADMIN_MODE
        settings_btn.setVisible(ADMIN_MODE)
        bottom.addWidget(settings_btn)

        # ── Bottom + footer grouped with no gap between them ──
        bottom_group = QVBoxLayout()
        bottom_group.setSpacing(0)
        bottom_group.setContentsMargins(0, 0, 0, 0)
        bottom_group.addLayout(bottom)
        main.addLayout(bottom_group)

        # Tab bar signals
        self.task_sidebar.task_switched.connect(self._switch_task)
        self.task_sidebar.task_deleted.connect(self._delete_task)
        self.task_sidebar.task_created.connect(self._create_task)

        # Dev preview shortcuts — Cmd+1/2/3
        QShortcut(QKeySequence("Ctrl+1"), self).activated.connect(self.right_panel.show_idle)
        QShortcut(QKeySequence("Ctrl+2"), self).activated.connect(self._dev_preview_running)
        QShortcut(QKeySequence("Ctrl+3"), self).activated.connect(self._dev_preview_done)

    # ── Handlers ─────────────────────────────────────────────

    def _on_start(self):
        task_idx = self._current_idx
        task = self._tasks[task_idx]

        sellpoint = self.left_panel.get_sellpoint()
        if not sellpoint:
            self.right_panel.show_running()
            self.right_panel.status_label.setText("请先填写卖点文案")
            return

        images = self.left_panel.get_image_paths()
        worker = PipelineWorker(sellpoint, images, task.name, self._video_model, self._kling_mode)
        task.worker = worker

        # Route all signals through task index
        worker.progress.connect(
            lambda s, st, d, i=task_idx: self._on_task_progress(i, s, st, d))
        worker.log.connect(
            lambda msg, i=task_idx: self._on_task_log(i, msg))
        worker.run_dirs_ready.connect(
            lambda od, i=task_idx: self._on_task_run_dirs(i, od))
        worker.pipeline_done.connect(
            lambda r, i=task_idx: self._on_task_finished(i, r))
        worker.error.connect(
            lambda msg, i=task_idx: self._on_task_error(i, msg))
        # QThread.finished fires AFTER run() returns — safe point to release Python ref
        worker.finished.connect(
            lambda t=task: setattr(t, 'worker', None))

        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.files_btn.setEnabled(False)
        task.panel_state = 1
        self.task_sidebar.set_progress(task_idx, 0.01)
        self.right_panel.show_running()
        # 通过全局任务队列启动（不直接 start()，由队列控制并发）
        self._task_queue.submit(worker)
        # Fold current input panel → then create & reveal a new blank task
        if len(self._tasks) < MAX_TASKS:
            self._fold_then_new_task()
        else:
            self._collapse_left_panel()

    def _on_stop(self):
        task = self._tasks[self._current_idx]
        if task.worker and task.worker.isRunning():
            task.worker.stop()
            self.right_panel.append_log("正在停止（当前步骤完成后保存进度）…")
            self.stop_btn.setEnabled(False)

    def _on_open_files(self):
        output_dir = self._result.get("output_dir", "")
        if not output_dir:
            return
        # output_dir is root during run, final folder after completion
        target = output_dir
        import subprocess, platform
        if platform.system() == "Darwin":
            subprocess.Popen(["open", target])
        elif platform.system() == "Windows":
            subprocess.Popen(["explorer", target])
        else:
            subprocess.Popen(["xdg-open", target])

    def _on_model_select(self):
        """视频模型切换菜单。"""
        from PySide6.QtWidgets import QMenu
        from PySide6.QtGui import QAction

        # (video_model, kling_mode, 显示标签)
        _OPTIONS = [
            ("kling",    "std",  "Kling 标准版 Std"),
            ("kling",    "pro",  "Kling 专业版 Pro"),
            None,  # 分隔线
            ("veo_fast", "",     "VEO 快速 4K  (2积分/片)"),
            ("veo_hq",   "",     "VEO 高质量 4K  (5积分/片)"),
        ]

        menu = QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{ background: white; border: 1px solid {BORDER};
                    border-radius: 8px; padding: 4px 0; }}
            QMenu::item {{ padding: 6px 20px; font-size: 12px; color: {TEXT_PRIMARY}; }}
            QMenu::item:selected {{ background: {CARD_BG}; }}
            QMenu::item:checked {{ color: {ACCENT}; font-weight: 600; }}
            QMenu::separator {{ height: 1px; background: {BORDER}; margin: 3px 10px; }}
        """)

        for opt in _OPTIONS:
            if opt is None:
                menu.addSeparator()
                continue
            vm, km, label = opt
            act = QAction(label, self)
            act.setCheckable(True)
            act.setChecked(self._video_model == vm and self._kling_mode == km)
            act.setData((vm, km, label))
            menu.addAction(act)

        chosen = menu.exec(self._model_btn.mapToGlobal(
            self._model_btn.rect().bottomLeft()
        ))
        if chosen and chosen.data():
            vm, km, label = chosen.data()
            self._video_model = vm
            self._kling_mode  = km
            short = label.split("  ")[0]
            self._model_btn.setText(f"视频模型：{short}  ▾")

    def _on_open_settings(self):
        """并发设置弹窗 — 运行时调整 Kling 槽位数和 pipeline 并发数。"""
        from utils.kling_client import kling_semaphore

        dlg = QDialog(self)
        dlg.setWindowTitle("并发设置")
        dlg.setFixedWidth(280)

        form = QFormLayout(dlg)
        form.setContentsMargins(20, 20, 20, 20)
        form.setSpacing(12)

        kling_spin = QSpinBox()
        kling_spin.setRange(1, 20)
        kling_spin.setValue(kling_semaphore.limit)
        kling_spin.setToolTip("推荐：4 人用 5，2 人用 10，10 人以上用 2")
        form.addRow("可灵并发槽位", kling_spin)

        task_spin = QSpinBox()
        task_spin.setRange(1, MAX_TASKS)
        task_spin.setValue(self._task_queue.limit)
        task_spin.setToolTip("同时运行的完整 pipeline 数量")
        form.addRow("Pipeline 并发数", task_spin)

        note = QLabel("修改立即生效，无需重启。\n可灵推荐值 = 20 / 使用人数。")
        note.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 11px;")
        note.setWordWrap(True)
        form.addRow(note)

        ok_btn = QPushButton("确定")
        ok_btn.clicked.connect(dlg.accept)
        form.addRow(ok_btn)

        def _apply():
            kling_semaphore.set_limit(kling_spin.value())
            self._task_queue.set_limit(task_spin.value())

        kling_spin.valueChanged.connect(lambda _: _apply())
        task_spin.valueChanged.connect(lambda _: _apply())

        dlg.exec()

    # Forward Cmd+V to the image grid when focus is elsewhere
    def keyPressEvent(self, e):
        is_paste = (
            e.matches(QKeySequence.Paste) or
            (e.key() == Qt.Key_V and bool(e.modifiers() & Qt.MetaModifier)) or
            (e.key() == Qt.Key_V and bool(e.modifiers() & Qt.ControlModifier))
        )
        if is_paste and not self.left_panel.text_edit.hasFocus():
            self.left_panel.image_grid._paste_from_clipboard()
        else:
            super().keyPressEvent(e)

    def _dev_preview_running(self):
        """Dev: simulate pipeline running through all steps."""
        self.right_panel.show_running()
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        steps = list(STEP_LABELS.keys())
        done = [0]

        def tick():
            i = done[0]
            if i < len(steps):
                self.right_panel.on_step_progress(steps[i], "started", "")
                done[0] += 1
            else:
                timer.stop()
                self.start_btn.setEnabled(True)
                self.stop_btn.setEnabled(False)

        timer = QTimer(self)
        timer.timeout.connect(tick)
        timer.start(900)

    def _dev_preview_done(self):
        """Dev: jump straight to done state with the latest output video."""
        import glob
        videos = sorted(
            glob.glob(str(
                Path(__file__).resolve().parent.parent / "output" / "**" / "final" / "*.mp4"
            ), recursive=True),
            key=lambda p: Path(p).stat().st_mtime, reverse=True
        )
        mp4 = videos[0] if videos else ""
        self._result["output_dir"] = str(Path(mp4).parent.parent) if mp4 else ""
        self.right_panel.show_done(mp4)
        self.files_btn.setEnabled(bool(mp4))
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)


# ── Entry point ───────────────────────────────────────────────
def main():
    if getattr(sys, "frozen", False):
        bundle_dir = Path(sys.executable).parent
        sys.path.insert(0, str(bundle_dir))

    app = QApplication(sys.argv)
    app.setStyleSheet(_app_stylesheet())

    if sys.platform == "darwin":
        app.setFont(QFont(".AppleSystemUIFont", 14))

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
