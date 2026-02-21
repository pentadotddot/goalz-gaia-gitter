"""Parse code payloads from a ClickUp task description.

Supports two formats:

1. **JSON payload** (preferred) – a fenced ``json`` code block containing a
   dict with at least a ``files`` key and optionally ``github_repo``,
   ``github_branch``, ``base_branch``, ``commit_message``, ``create_pr``,
   ``pr_target_branch``.

2. **Markdown code blocks** (legacy fallback) – ``## <filepath>`` headings
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

# Matches a fenced ```json ... ``` block and captures the content.
_JSON_BLOCK_RE = re.compile(
    r"```json\s*\n"    # opening fence with json tag
    r"(.*?)"           # captured JSON content (non-greedy)
    r"\n\s*```",        # closing fence
    re.DOTALL,
)


def parse_json_payload(description: str) -> dict[str, Any] | None:
    """Extract a JSON code block from *description* and return it as a dict.

    Returns ``None`` if no valid JSON block is found.  The returned dict may
    contain any of the ``PushCodeRequest`` fields (``github_repo``,
    ``github_branch``, ``base_branch``, ``files``, ``commit_message``,
    ``create_pr``, ``pr_target_branch``).
    """
    match = _JSON_BLOCK_RE.search(description)
    if not match:
        return None

    raw = match.group(1).strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Found a ```json block but it is not valid JSON")
        return None

    if not isinstance(data, dict):
        logger.warning("JSON block is not a dict – ignoring")
        return None

    # Must have at least a "files" key to be useful
    if "files" not in data or not isinstance(data["files"], dict):
        logger.warning("JSON block has no valid 'files' dict – ignoring")
        return None

    return data


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
