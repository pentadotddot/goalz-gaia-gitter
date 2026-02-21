"""Parse code payloads from a ClickUp task description.

Supports three formats (tried in order):

1. **Fenced JSON** – a ` ```json ``` ` code block containing the payload.
2. **Raw JSON** – a bare ``{ ... }`` object pasted directly into the
   description (the natural output of a superagent).
3. **Markdown code blocks** (legacy fallback) – ``## <filepath>`` headings
   each followed by a fenced code block.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# JSON payload extraction
# ---------------------------------------------------------------------------

# 1) Fenced ```json ... ``` block.
_JSON_BLOCK_RE = re.compile(
    r"```json\s*\n"    # opening fence with json tag
    r"(.*?)"           # captured JSON content (non-greedy)
    r"\n\s*```",        # closing fence
    re.DOTALL,
)

# 2) Raw JSON object – find the first `{` and match to its closing `}`.
#    We use a simple approach: locate the first `{` then try progressively
#    larger slices until json.loads succeeds.  This handles nested braces
#    correctly without a complex regex.


def _extract_raw_json(text: str) -> dict[str, Any] | None:
    """Try to find and parse a raw JSON object ``{ ... }`` in *text*."""
    start = text.find("{")
    if start == -1:
        return None

    # Walk backwards from the end to find the last `}`
    end = text.rfind("}")
    if end == -1 or end <= start:
        return None

    candidate = text[start : end + 1]
    try:
        data = json.loads(candidate)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass

    return None


def _validate_payload(data: dict[str, Any]) -> dict[str, Any] | None:
    """Return *data* if it looks like a valid code-push payload, else None."""
    if not isinstance(data, dict):
        logger.warning("JSON payload is not a dict – ignoring")
        return None
    if "files" not in data or not isinstance(data["files"], dict):
        logger.warning("JSON payload has no valid 'files' dict – ignoring")
        return None
    return data


def parse_json_payload(description: str) -> dict[str, Any] | None:
    """Extract a JSON payload from *description* and return it as a dict.

    Tries fenced code blocks first, then falls back to raw JSON detection.

    Returns ``None`` if no valid JSON payload is found.  The returned dict
    may contain any of the ``PushCodeRequest`` fields (``github_repo``,
    ``github_branch``, ``base_branch``, ``files``, ``commit_message``,
    ``create_pr``, ``pr_target_branch``).
    """
    # --- Attempt 1: fenced ```json ... ``` block -------------------------
    match = _JSON_BLOCK_RE.search(description)
    if match:
        raw = match.group(1).strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Found a ```json block but it is not valid JSON")
        else:
            result = _validate_payload(data)
            if result is not None:
                logger.info("Parsed JSON payload from fenced code block")
                return result

    # --- Attempt 2: raw JSON object { ... } ------------------------------
    data = _extract_raw_json(description)
    if data is not None:
        result = _validate_payload(data)
        if result is not None:
            logger.info("Parsed JSON payload from raw JSON in description")
            return result

    return None


# ---------------------------------------------------------------------------
# Legacy: markdown code block extraction
# ---------------------------------------------------------------------------

# Regex breakdown:
#   ^## \s* (.+?)          – a level-2 heading whose text is the file path
#   \s*                     – optional whitespace / blank lines
#   ```[^\n]*\n             – opening fence (``` optionally followed by a language tag)
#   (.*?)                   – captured code content (non-greedy)
#   \n```                   – closing fence
_BLOCK_RE = re.compile(
    r"^##\s+(.+?)\s*$"       # heading with file path
    r"\s*"                    # optional whitespace between heading and fence
    r"```[^\n]*\n"            # opening fence
    r"(.*?)"                  # code content
    r"\n```",                 # closing fence
    re.MULTILINE | re.DOTALL,
)


def parse_code_blocks(description: str) -> dict[str, str]:
    """Return a mapping of ``{file_path: code_content}`` extracted from *description*.

    Strips leading/trailing whitespace from both keys and values.
    """
    results: dict[str, str] = {}
    for match in _BLOCK_RE.finditer(description):
        file_path = match.group(1).strip()
        code = match.group(2)
        # Preserve internal whitespace but strip one leading/trailing newline
        # that typically exists right after/before the fences.
        results[file_path] = code.strip("\n")
    return results
