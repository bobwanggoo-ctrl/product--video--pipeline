#!/usr/bin/env python3
"""Product Video Pipeline - Main Entry Point.

Usage:
    python main.py              # 半自动模式（逐步确认）
    python main.py --auto       # 全自动模式（无人值守）
"""

import argparse
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

from config import settings
from config.settings import create_run_dirs
from pipeline.orchestrator import PipelineOrchestrator, PipelineState

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(settings.LOGS_DIR / "pipeline.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


def _find_checkpoints() -> list[Path]:
    """Scan output/ for unfinished checkpoint files."""
    checkpoints = []
    if not settings.OUTPUT_DIR.exists():
        return checkpoints
    for cp in settings.OUTPUT_DIR.glob("*/checkpoint.json"):
        try:
            state = PipelineState.load(cp)
            has_pending = any(
                s.status.value in ("pending", "in_progress", "awaiting_confirm")
                for s in state.steps.values()
            )
            if has_pending:
                checkpoints.append(cp)
        except Exception:
            continue
    return checkpoints


def _read_sellpoint(input_dir: Path) -> str:
    """Read sellpoint text from docx or txt in the input directory."""
    # Try docx first
    docx_files = list(input_dir.glob("*.docx"))
    if docx_files:
        from docx import Document
        doc = Document(str(docx_files[0]))
        text = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
        if text:
            return text

    # Fallback to txt
    txt_files = list(input_dir.glob("*.txt"))
    if txt_files:
        return txt_files[0].read_text(encoding="utf-8").strip()

    return ""


def _list_input_dirs() -> list[Path]:
    """List available input directories."""
    if not settings.INPUT_DIR.exists():
        return []
    return sorted(
        d for d in settings.INPUT_DIR.iterdir()
        if d.is_dir() and d.name != "music" and not d.name.startswith(".")
    )


def _resume_pipeline(checkpoint_path: Path):
    """Resume a pipeline from checkpoint."""
    state = PipelineState.load(checkpoint_path)
    run_dir = checkpoint_path.parent
    run_dirs = {
        "root": run_dir,
        "storyboard": run_dir / "storyboard.json",
        "sellpoint": run_dir / "sellpoint.txt",
        "frames": run_dir / "frames",
        "videos": run_dir / "videos",
        "final": run_dir / "final",
        "checkpoint": checkpoint_path,
    }

    # Read sellpoint from saved file
    sellpoint_text = ""
    if run_dirs["sellpoint"].exists():
        sellpoint_text = run_dirs["sellpoint"].read_text(encoding="utf-8").strip()

    initial_input = {
        "sellpoint_text": sellpoint_text,
        "reference_image_dir": "",  # Not needed for resume (frames already generated)
        "bgm_dir": str(settings.MUSIC_DIR) if settings.MUSIC_DIR.exists() else "",
        "font_dir": str(settings.FONTS_DIR) if settings.FONTS_DIR.exists() else "",
    }

    orchestrator = PipelineOrchestrator(state)
    return orchestrator.run_all(initial_input, run_dirs)


def main():
    """Main pipeline entry point."""
    parser = argparse.ArgumentParser(description="Product Video Pipeline")
    parser.add_argument("--auto", action="store_true", help="全自动模式（无人值守）")
    args = parser.parse_args()

    mode = "full_auto" if args.auto else "semi_auto"

    settings.LOGS_DIR.mkdir(parents=True, exist_ok=True)

    print("\n=== Product Video Pipeline ===")
    print(f"模式: {'全自动' if args.auto else '半自动'}\n")

    # 1. Check for unfinished checkpoints
    checkpoints = _find_checkpoints()
    if checkpoints:
        print("发现未完成的任务:")
        for i, cp in enumerate(checkpoints):
            print(f"  [{i + 1}] {cp.parent.name}")
        print(f"  [n] 开始新任务")
        choice = input("\n选择: ").strip().lower()
        if choice.isdigit() and 1 <= int(choice) <= len(checkpoints):
            cp = checkpoints[int(choice) - 1]
            print(f"\n恢复任务: {cp.parent.name}")
            start = time.time()
            result = _resume_pipeline(cp)
            _print_summary(result, time.time() - start)
            return

    # 2. Collect input
    print("--- 输入配置 ---")
    input_dirs = _list_input_dirs()
    if input_dirs:
        print("可用输入目录:")
        for i, d in enumerate(input_dirs):
            print(f"  [{i + 1}] {d.name}")
        idx = input(f"\n选择输入目录 [默认: 1]: ").strip()
        idx = int(idx) if idx.isdigit() and 1 <= int(idx) <= len(input_dirs) else 1
        input_dir = input_dirs[idx - 1]
    else:
        input_path = input("输入目录路径: ").strip()
        input_dir = Path(input_path)

    if not input_dir.exists():
        print(f"[!] 目录不存在: {input_dir}")
        return

    # Read sellpoint
    sellpoint_text = _read_sellpoint(input_dir)
    if not sellpoint_text:
        print(f"[!] 未找到卖点文案 (docx/txt): {input_dir}")
        return

    print(f"\n输入目录: {input_dir}")
    print(f"卖点文案: {len(sellpoint_text)} 字符")
    print(f"前 80 字: {sellpoint_text[:80]}...")

    # Check reference images
    img_exts = {".jpg", ".jpeg", ".png", ".webp"}
    ref_images = [f for f in input_dir.iterdir() if f.suffix.lower() in img_exts]
    print(f"参考图: {len(ref_images)} 张")

    if not ref_images:
        print("[!] 未找到参考图，Skill 2 生图质量可能受影响")

    confirm = input("\n确认开始? [Y/n] ").strip().lower()
    if confirm == "n":
        print("已取消")
        return

    # 3. Create run
    task_id = f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    run_dirs = create_run_dirs(task_id)

    # Save sellpoint text
    run_dirs["sellpoint"].write_text(sellpoint_text, encoding="utf-8")

    state = PipelineState(task_id=task_id, mode=mode)
    orchestrator = PipelineOrchestrator(state)

    initial_input = {
        "sellpoint_text": sellpoint_text,
        "reference_image_dir": str(input_dir),
        "bgm_dir": str(settings.MUSIC_DIR) if settings.MUSIC_DIR.exists() else "",
        "font_dir": str(settings.FONTS_DIR) if settings.FONTS_DIR.exists() else "",
        "title_templates_dir": str(settings.FCP_TITLES_DIR) if settings.FCP_TITLES_DIR.exists() else "",
    }

    # 4. Run
    start = time.time()
    logger.info(f"Pipeline started: {task_id}")
    logger.info(f"Input: {input_dir}")
    logger.info(f"Output: {run_dirs['root']}")

    result = orchestrator.run_all(initial_input, run_dirs)

    # 5. Summary
    _print_summary(result, time.time() - start)


def _print_summary(result: dict, elapsed: float):
    """Print final pipeline summary."""
    print(f"\n{'=' * 50}")

    if result.get("aborted"):
        print("任务已暂停，进度已保存，下次启动时可继续。")
    else:
        print("Pipeline 完成!")
        print(f"  耗时: {elapsed:.0f}s ({elapsed / 60:.1f}min)")
        print(f"  成片: {result.get('mp4', 'N/A')}")
        print(f"  字幕(EN): {result.get('srt_en', 'N/A')}")
        print(f"  字幕(CN): {result.get('srt_cn', 'N/A')}")
        print(f"  剪映: {result.get('jianying_json', 'N/A')}")
        print(f"  FCPXML: {result.get('fcpxml', 'N/A')}")

    print(f"{'=' * 50}\n")


if __name__ == "__main__":
    main()
