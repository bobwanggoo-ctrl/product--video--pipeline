"""首次运行向导 — PySide6 图形界面收集 API Key，写入 .env。

由 tools/bootstrap_mac.sh 或 tools/bootstrap_win.ps1 调用。
执行时机：.env 不存在 或 .env 里 AI_NAV_TOKEN 为空。
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
ENV_EXAMPLE = ROOT / ".env.example"


class FirstRunWizard(QDialog):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("产品视频流水线 - 首次配置")
        self.setMinimumWidth(520)

        layout = QVBoxLayout(self)

        intro = QLabel(
            "<h3>欢迎！请填写 API Key 以开始使用</h3>"
            "<p>以下 Key 只保存在你的本机 <code>.env</code> 文件中，不会上传。<br>"
            "填完后点击下方按钮即可。</p>"
        )
        intro.setWordWrap(True)
        intro.setTextFormat(Qt.RichText)
        layout.addWidget(intro)

        form = QFormLayout()
        form.setVerticalSpacing(12)
        form.setHorizontalSpacing(12)

        self.ai_nav = self._make_input("粘贴 AI 导航 Token…")
        self.kling_ak = self._make_input("Kling Access Key")
        self.kling_sk = self._make_input("Kling Secret Key", password=True)
        self.google_vision = self._make_input("可留空（侵权检测用）")

        form.addRow(self._label("AI 导航 Token", required=True), self.ai_nav)
        form.addRow(self._label("Kling Access Key", required=True), self.kling_ak)
        form.addRow(self._label("Kling Secret Key", required=True), self.kling_sk)
        form.addRow(self._label("Google Vision API Key", required=False), self.google_vision)

        layout.addLayout(form)

        help_label = QLabel(
            '<p style="color:#666;font-size:12px;">'
            "不知道 Key 从哪来？"
            '<a href="https://yswg.love">AI 导航后台</a> · '
            '<a href="https://klingai.com">Kling 控制台</a> · '
            '<a href="https://cloud.google.com/vision">Google Vision</a>'
            "</p>"
        )
        help_label.setOpenExternalLinks(True)
        help_label.setTextInteractionFlags(Qt.TextBrowserInteraction)
        layout.addWidget(help_label)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        save_btn = QPushButton("保存并启动")
        save_btn.setDefault(True)
        save_btn.clicked.connect(self._on_save)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(save_btn)
        layout.addLayout(btn_row)

        self._prefill()

    @staticmethod
    def _label(text: str, required: bool) -> QLabel:
        suffix = ' <span style="color:#d33;">*</span>' if required else ''
        lbl = QLabel(f"{text}{suffix}")
        lbl.setTextFormat(Qt.RichText)
        return lbl

    @staticmethod
    def _make_input(placeholder: str, password: bool = False) -> QLineEdit:
        le = QLineEdit()
        le.setPlaceholderText(placeholder)
        if password:
            le.setEchoMode(QLineEdit.Password)
        return le

    def _prefill(self) -> None:
        """如果 .env 已存在，预填已有的值（方便补充可选项）。"""
        if not ENV_PATH.exists():
            return
        values = _parse_env(ENV_PATH)
        self.ai_nav.setText(values.get("AI_NAV_TOKEN", ""))
        self.kling_ak.setText(values.get("KLING_ACCESS_KEY", ""))
        self.kling_sk.setText(values.get("KLING_SECRET_KEY", ""))
        self.google_vision.setText(values.get("GOOGLE_VISION_API_KEY", ""))

    def _on_save(self) -> None:
        ai_nav = self.ai_nav.text().strip()
        kling_ak = self.kling_ak.text().strip()
        kling_sk = self.kling_sk.text().strip()
        google_vision = self.google_vision.text().strip()

        missing = []
        if not ai_nav:
            missing.append("AI 导航 Token")
        if not kling_ak:
            missing.append("Kling Access Key")
        if not kling_sk:
            missing.append("Kling Secret Key")
        if missing:
            QMessageBox.warning(
                self,
                "必填项未完成",
                "以下字段必须填写：\n\n  - " + "\n  - ".join(missing),
            )
            return

        # 确保 .env 存在（从 .env.example 复制）
        if not ENV_PATH.exists():
            if ENV_EXAMPLE.exists():
                shutil.copy(ENV_EXAMPLE, ENV_PATH)
            else:
                ENV_PATH.write_text("", encoding="utf-8")

        _update_env_key(ENV_PATH, "AI_NAV_TOKEN", ai_nav)
        _update_env_key(ENV_PATH, "KLING_ACCESS_KEY", kling_ak)
        _update_env_key(ENV_PATH, "KLING_SECRET_KEY", kling_sk)
        _update_env_key(ENV_PATH, "GOOGLE_VISION_API_KEY", google_vision)

        self.accept()


def _parse_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip()
    return out


def _update_env_key(path: Path, key: str, value: str) -> None:
    """就地更新 .env 文件里的 key=value，保留原文件结构（注释/顺序）。"""
    lines = path.read_text(encoding="utf-8").splitlines()
    updated = False
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith(f"{key}=") or stripped.startswith(f"{key} ="):
            lines[i] = f"{key}={value}"
            updated = True
            break
    if not updated:
        lines.append(f"{key}={value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    app = QApplication(sys.argv)
    dlg = FirstRunWizard()
    result = dlg.exec()
    return 0 if result == QDialog.Accepted else 1


if __name__ == "__main__":
    sys.exit(main())
