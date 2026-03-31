#!/usr/bin/env python3
"""Product Video Pipeline - Main Entry Point.

Semi-auto mode: runs each step with user confirmation.
Usage: python main.py
"""

import logging
import sys
from pathlib import Path

from config import settings
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


def main():
    """Main pipeline entry point."""
    logger.info("Product Video Pipeline started.")
    logger.info(f"Output directory: {settings.OUTPUT_DIR}")

    # Ensure output directories exist
    for d in [settings.STORYBOARDS_DIR, settings.FRAMES_DIR,
              settings.VIDEOS_DIR, settings.FINAL_DIR, settings.LOGS_DIR]:
        d.mkdir(parents=True, exist_ok=True)

    state = PipelineState(mode="semi_auto")
    orchestrator = PipelineOrchestrator(state)

    logger.info("Pipeline orchestrator ready. Skills will be connected in subsequent steps.")
    print("\n=== Product Video Pipeline ===")
    print("Status: Skeleton ready. Implement skills step by step.")
    print("See PROGRESS.md for current progress.\n")


if __name__ == "__main__":
    main()
