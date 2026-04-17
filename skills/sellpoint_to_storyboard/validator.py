"""Storyboard output validation based on storyboard_rules.md hard constraints."""

import re
from typing import Any

# Valid shot types
VALID_SHOT_TYPES = {"Wide", "Medium", "Close", "Macro"}


def validate_storyboard(data: dict[str, Any]) -> tuple[bool, list[str]]:
    """Validate storyboard dict against hard constraints A1-A8.

    Returns:
        (ok, errors): ok=True if no hard-constraint violations.
        Soft warnings are prefixed with [WARN], hard errors with [FAIL].
    """
    errors: list[str] = []

    scene_groups = data.get("scene_groups", [])

    # --- A2: Product type classification ---
    product_type = data.get("product_type", "")
    if product_type not in ("Type A", "Type B"):
        errors.append(
            f"[FAIL] A2: product_type = '{product_type}', expected 'Type A' or 'Type B'"
        )

    product_type_reason = data.get("product_type_reason", "")
    if not product_type_reason or len(product_type_reason) < 5:
        errors.append("[FAIL] A2: product_type_reason is missing or too short")

    # --- A3: Model profile ---
    model_profile = data.get("model_profile", "")
    if not model_profile or len(model_profile) < 3:
        errors.append("[FAIL] A3: model_profile is missing or too short")

    # --- A1: Output structure ---
    num_groups = len(scene_groups)
    if num_groups < 4 or num_groups > 5:
        errors.append(f"[FAIL] A1: scene_groups count = {num_groups}, expected 4-5")

    all_shots = []
    for sg in scene_groups:
        shots = sg.get("shots", [])
        num_shots_in_group = len(shots)
        if num_shots_in_group < 1 or num_shots_in_group > 4:
            errors.append(
                f"[FAIL] A1: scene_group '{sg.get('name', '?')}' has {num_shots_in_group} shots, expected 1-4"
            )
        all_shots.extend(shots)

    total_shots = len(all_shots)
    if total_shots != 15:
        errors.append(f"[FAIL] A1: total shots = {total_shots}, expected 15")

    # --- A2: Shot type distribution ---
    close_macro_count = sum(1 for s in all_shots if s.get("type") in ("Close", "Macro"))
    if close_macro_count > 5:
        errors.append(
            f"[FAIL] A2: Close/Macro shots = {close_macro_count}, max allowed 5"
        )

    for s in all_shots:
        shot_type = s.get("type", "")
        if shot_type not in VALID_SHOT_TYPES:
            errors.append(
                f"[FAIL] A2: shot {s.get('shot_id', '?')} has invalid type '{shot_type}'"
            )

    # --- A4: Format & cleanliness ---
    bracket_pattern = re.compile(r"\[.*?\]")
    for s in all_shots:
        prompt = s.get("prompt_cn", "")
        sid = s.get("shot_id", "?")

        # Check for unresolved brackets
        brackets = bracket_pattern.findall(prompt)
        if brackets:
            errors.append(
                f"[FAIL] A4: shot {sid} prompt_cn contains brackets: {brackets[:3]}"
            )

        # Check for English product names (simple heuristic: consecutive ASCII words)
        if re.search(r"[A-Za-z]{3,}(?:\s+[A-Za-z]{3,}){2,}", prompt):
            # Allow common English terms like "no logo", "Bokeh", "Wide"
            cleaned = re.sub(r"\(no logo\)", "", prompt, flags=re.IGNORECASE)
            if re.search(r"[A-Za-z]{3,}(?:\s+[A-Za-z]{3,}){2,}", cleaned):
                errors.append(
                    f"[WARN] A4: shot {sid} prompt_cn may contain untranslated English"
                )

        # Must end with (no logo)
        if not prompt.rstrip().endswith("(no logo)"):
            errors.append(f"[FAIL] A4: shot {sid} prompt_cn does not end with '(no logo)'")

    # --- A5: Environment anchor reuse ---
    for sg in scene_groups:
        anchor = sg.get("environment_anchor", "")
        if not anchor:
            errors.append(
                f"[FAIL] A5: scene_group '{sg.get('name', '?')}' missing environment_anchor"
            )

    has_fail = any(e.startswith("[FAIL]") for e in errors)
    return (not has_fail, errors)
