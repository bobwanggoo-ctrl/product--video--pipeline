"""JSON extraction and repair utilities for LLM output."""

import json
import re
import logging

logger = logging.getLogger(__name__)


def repair_json(s: str) -> str:
    """Repair truncated or malformed JSON: remove trailing commas, balance brackets."""
    # Remove trailing commas before } or ]
    s = re.sub(r",\s*([}\]])", r"\1", s)

    try:
        json.loads(s)
        return s
    except json.JSONDecodeError:
        pass

    # Trim incomplete tokens at end
    s_trimmed = s.rstrip()
    if s_trimmed and s_trimmed[-1] not in ('}', ']', '"', '0', '1', '2', '3',
                                            '4', '5', '6', '7', '8', '9',
                                            'e', 'l', 'u'):
        for i in range(len(s_trimmed) - 1, -1, -1):
            if s_trimmed[i] in (',', '}', ']'):
                s_trimmed = s_trimmed[:i + 1]
                break

    s_trimmed = re.sub(r",\s*$", "", s_trimmed)

    # Track brackets with a stack, close unclosed ones
    stack = []
    in_string = False
    escape = False
    for ch in s_trimmed:
        if escape:
            escape = False
            continue
        if ch == '\\' and in_string:
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == '{':
            stack.append('}')
        elif ch == '[':
            stack.append(']')
        elif ch in ('}', ']') and stack and stack[-1] == ch:
            stack.pop()

    if in_string:
        s_trimmed += '"'
    s_trimmed += ''.join(reversed(stack))
    return s_trimmed


def extract_json(text: str) -> dict:
    """Extract JSON from LLM output, handling markdown code blocks and truncation."""
    if not text or not isinstance(text, str):
        raise ValueError("Empty content, cannot extract JSON.")

    # Try markdown code block
    code_block = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL | re.IGNORECASE)
    if code_block:
        candidate = code_block.group(1).strip()
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

    # Clean markdown markers
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    cleaned = cleaned.strip()

    # Direct parse
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Find outermost { ... }
    first = cleaned.find("{")
    last = cleaned.rfind("}")
    if first != -1 and last > first:
        candidate = cleaned[first:last + 1]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass
        # Repair and retry
        repaired = repair_json(candidate)
        try:
            return json.loads(repaired)
        except json.JSONDecodeError as e:
            logger.error(f"JSON parse failed after repair: {candidate[:300]}")
            raise ValueError(f"Cannot parse JSON from LLM output: {e}") from e

    raise ValueError("No valid JSON structure found in LLM output.")
