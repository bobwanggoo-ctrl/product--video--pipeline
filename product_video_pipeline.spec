# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for Product Video Pipeline GUI.

Usage:
  Mac:     pyinstaller product_video_pipeline.spec --noconfirm
  Windows: pyinstaller product_video_pipeline.spec --noconfirm
"""

import sys
from pathlib import Path

ROOT = Path(SPECPATH)

# ── Source data to bundle ─────────────────────────────────────
# (src, dest_inside_bundle)
datas = [
    (str(ROOT / "assets"),           "assets"),
    (str(ROOT / "skills"),           "skills"),
    (str(ROOT / "models"),           "models"),
    (str(ROOT / "utils"),            "utils"),
    (str(ROOT / "config"),           "config"),
    (str(ROOT / "pipeline"),         "pipeline"),
    (str(ROOT / "gui"),              "gui"),
    (str(ROOT / ".env.example"),     "."),
]

# ── Hidden imports needed by dynamic skill imports ───────────
hiddenimports = [
    "skills.sellpoint_to_storyboard",
    "skills.sellpoint_to_storyboard.converter",
    "skills.sellpoint_to_storyboard.validator",
    "skills.storyboard_to_frame",
    "skills.storyboard_to_frame.generator",
    "skills.compliance_checker",
    "skills.compliance_checker.checker",
    "skills.compliance_checker.copyright_checker",
    "skills.compliance_checker.prompts",
    "skills.frame_to_video",
    "skills.frame_to_video.generator",
    "skills.frame_to_video.motion_planner",
    "skills.auto_editor",
    "skills.auto_editor.ffmpeg_assembler",
    "skills.auto_editor.llm_editor",
    "skills.auto_editor.subtitle_gen",
    "skills.auto_editor.font_scanner",
    "skills.auto_editor.bgm_scanner",
    "skills.auto_editor.title_scanner",
    "skills.auto_editor.video_analyzer",
    "skills.auto_editor.vision_checker",
    "skills.auto_editor.edl_exporter",
    "models.storyboard",
    "models.compliance",
    "models.timeline",
    "models.video_clip",
    "pipeline.orchestrator",
    "pipeline.frame_selector",
    "utils.llm_client",
    "utils.ai_nav_client",
    "utils.kling_client",
    "utils.ffmpeg_wrapper",
    "utils.json_repair",
    "utils.trace_logger",
    "config.settings",
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "docx",
    "PIL",
    "pydantic",
    "dotenv",
    "openai",
    "requests",
    "jwt",
]

# ── Excludes (reduce bundle size) ────────────────────────────
excludes = [
    "tkinter",
    "matplotlib",
    "numpy",
    "scipy",
    "pandas",
    "IPython",
    "notebook",
    "pytest",
]

block_cipher = None

a = Analysis(
    [str(ROOT / "gui" / "app.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ProductVideoPipeline",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,   # No terminal window
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "assets" / "icon.icns") if sys.platform == "darwin" else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="ProductVideoPipeline",
)

# macOS: wrap in .app bundle
if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="ProductVideoPipeline.app",
        icon=str(ROOT / "assets" / "icon.icns"),
        bundle_identifier="com.yswg.product-video-pipeline",
        info_plist={
            "CFBundleShortVersionString": "1.0.0",
            "CFBundleDisplayName": "Product Video Pipeline",
            "NSHighResolutionCapable": True,
            "NSMicrophoneUsageDescription": "",
        },
    )
