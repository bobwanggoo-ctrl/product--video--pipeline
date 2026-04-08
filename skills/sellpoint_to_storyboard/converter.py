"""Skill 1: Sellpoint → Storyboard conversion.

Migrated from sellpoint-to-video-agent/sellpoint_converter.py,
refactored to use unified LLM client and new data models.
"""

import json
import logging
import sys
from pathlib import Path
from typing import Optional

from models.storyboard import Storyboard
from utils.llm_client import llm_client
from utils.json_repair import extract_json
from .validator import validate_storyboard

logger = logging.getLogger(__name__)

RULES_DIR = Path(__file__).resolve().parent / "rules"
DEFAULT_RULES_PATH = RULES_DIR / "storyboard_rules.md"

# JSON schema appended to system prompt
JSON_SCHEMA = """
## 输出格式（必须严格返回以下 JSON 结构，不要包含其它内容）

你必须只输出一个合法的 JSON 对象，格式如下：

```json
{
  "product_type": "Type A 或 Type B",
  "product_type_reason": "判断理由（必须说明为什么属于节日/节气限定类或日常/功能通用类）",
  "model_profile": "根据产品受众分析得出，例如：28岁白人女性，休闲风格",
  "director_plan": {
    "tier_allocation": "Tier1: X镜头(核心卖点), Tier2: X镜头(次要), Tier3: X镜头(辅助)",
    "scene_1": "场景名: X 个镜头 -> 卖点 (静物/交互)",
    "scene_2": "..."
  },
  "scene_groups": [
    {
      "scene_group_id": 1,
      "name": "SCENE GROUP 1: 场景主题",
      "environment_anchor": "本组完整环境锚点（包含空间、光线、核心道具描述）",
      "shots": [
        {
          "shot_id": 1,
          "type": "Wide",
          "purpose": "整体氛围展示",
          "prompt_cn": "完整的中文提示词（套用SECTION D公式）"
        }
      ]
    }
  ]
}
```

**关键要求：**
1. 总共必须有且仅有 15 个 shots，分布在 4-5 个 scene_groups 中。
2. product_type 必须判断为 Type A（节日/节气限定类）或 Type B（日常/功能通用类），并给出具体判断理由。
3. model_profile 必须根据产品受众分析得出（男士→白人男性，女士→白人女性，通用→默认白人女性）。
4. director_plan 必须包含 Tier 1/2/3 的镜头分配规划。
5. environment_anchor 同一场景组内必须完全一致，包含完整的环境描述。
6. 只输出中文提示词（prompt_cn），不要输出英文。不要输出 markdown 代码块标记，直接输出纯 JSON。
7. prompt_cn 必须套用 SECTION D 的提示词公式，道具按景别递减法则填入。
"""


def load_rules(rules_path: Optional[Path] = None) -> str:
    """Load storyboard rules from markdown file."""
    path = rules_path or DEFAULT_RULES_PATH
    if not path.exists():
        raise FileNotFoundError(f"Rules file not found: {path}")
    return path.read_text(encoding="utf-8")


def build_system_prompt(rules: str) -> str:
    """Build system prompt from rules + JSON schema."""
    return rules.rstrip() + "\n\n" + JSON_SCHEMA


def build_user_message(sellpoint: str, compact: bool = False) -> str:
    """Build user message from sellpoint text."""
    suffix = "\n\n注意：请尽量精简输出，减少冗余描述，确保 JSON 紧凑。" if compact else ""
    return f"""# Input Information
以下是我的产品卖点（来源：亚马逊 Listing）：

---
{sellpoint.strip()}
---

请根据上述规则，分析卖点并输出 15 个分镜的完整 JSON。直接输出 JSON，不要有其它说明文字。{suffix}"""


def convert(
    sellpoint: str,
    *,
    rules_path: Optional[Path] = None,
    rules_content: Optional[str] = None,
    output_path: Optional[Path] = None,
    preferred_llm: Optional[str] = None,
    preferred_route: Optional[str] = None,
    max_retries: int = 2,
) -> Storyboard:
    """Convert sellpoint text to storyboard.

    Args:
        sellpoint: Product selling points text.
        rules_path: Custom rules file path. Defaults to built-in rules.
        rules_content: Direct rules text (overrides rules_path).
        output_path: Optional path to save JSON output.
        preferred_llm: 'gemini' | 'deepseek' | None (auto).
        preferred_route: 'service' | 'proxy' | 'auto' (Gemini only).
        max_retries: Max retries on JSON parse failure.

    Returns:
        Validated Storyboard model.
    """
    rules = rules_content or load_rules(rules_path)
    system_prompt = build_system_prompt(rules)

    last_err = None
    for attempt in range(1, max_retries + 2):
        compact = attempt > 1
        if compact:
            logger.info(f"[Converter] Retry {attempt}/{max_retries + 1}, compact mode enabled")

        user_message = build_user_message(sellpoint, compact=compact)

        try:
            raw = llm_client.call(
                system_prompt,
                user_message,
                preferred_llm=preferred_llm,
                preferred_route=preferred_route,
                json_mode=True,
            )
        except Exception as e:
            logger.error(f"[Converter] LLM call failed: {e}")
            raise

        try:
            data = extract_json(raw)
        except ValueError as e:
            last_err = e
            if attempt <= max_retries:
                logger.warning(f"[Converter] JSON parse failed (attempt {attempt}), retrying...")
                continue
            raise ValueError(f"Cannot parse JSON after {max_retries + 1} attempts: {e}") from e

        # Validate
        ok, errors = validate_storyboard(data)
        if not ok:
            logger.warning(f"[Converter] Validation warnings: {errors}")
            # 硬约束失败（如 0 个 scene_groups）→ 重试
            has_fail = any(e.startswith("[FAIL]") for e in errors)
            if has_fail and attempt <= max_retries:
                logger.warning(f"[Converter] 硬约束校验失败 (attempt {attempt})，重试...")
                last_err = ValueError(f"Validation failed: {errors}")
                continue

        # Build Storyboard model
        storyboard = Storyboard.model_validate(data)

        # Save if requested
        if output_path:
            output_path = Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            logger.info(f"[Converter] Saved to: {output_path}")

        logger.info(
            f"[Converter] Done: {len(storyboard.scene_groups)} groups, "
            f"{storyboard.total_shots} shots"
        )

        # 附带 trace 数据（供 TraceLogger 记录）
        storyboard._trace = {
            "system_prompt": system_prompt,
            "user_prompt": user_message,
            "llm_response": raw,
            "attempts": attempt,
        }

        return storyboard

    raise ValueError(f"Conversion failed: {last_err}")


def main():
    """CLI entry point for standalone testing."""
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    parser = argparse.ArgumentParser(description="Sellpoint → Storyboard Converter")
    parser.add_argument("input", nargs="?", help="Sellpoint text or .txt file path")
    parser.add_argument("-o", "--output", default=None, help="Output JSON path")
    parser.add_argument("-r", "--rules", default=None, help="Custom rules file path")
    parser.add_argument("--llm", default=None, choices=["gemini", "deepseek"], help="LLM choice")
    parser.add_argument("--route", default=None, choices=["service", "proxy", "auto"], help="Gemini route")
    args = parser.parse_args()

    # Parse input
    if args.input is None:
        print("Usage: python -m skills.sellpoint_to_storyboard.converter <sellpoint_text_or_file>")
        sys.exit(1)

    inp = args.input.strip()
    if "\n" not in inp and len(inp) < 260:
        p = Path(inp)
        if p.exists():
            sellpoint = p.read_text(encoding="utf-8")
        else:
            sellpoint = args.input
    else:
        sellpoint = args.input

    rules_path = Path(args.rules) if args.rules else None
    output_path = Path(args.output) if args.output else Path("output/storyboards/result.json")

    storyboard = convert(
        sellpoint,
        rules_path=rules_path,
        output_path=output_path,
        preferred_llm=args.llm,
        preferred_route=args.route,
    )
    print(f"\nConversion complete: {len(storyboard.scene_groups)} groups, {storyboard.total_shots} shots")


if __name__ == "__main__":
    main()
