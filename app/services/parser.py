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

# 1) Fenced ``` ... ``` block (any language tag – json, swift, text, etc.)
_JSON_BLOCK_RE = re.compile(
    r"```\w*\s*\n"     # opening fence with optional language tag
    r"(.*?)"           # captured JSON content (non-greedy)
    r"\n\s*```",        # closing fence
    re.DOTALL,
)

# 2) Raw JSON object – find the first `{` and match to its closing `}`.
#    We use a simple approach: locate the first `{` then try progressively
#    larger slices until json.loads succeeds.  This handles nested braces
#    correctly without a complex regex.


def _fix_content_quotes(text: str) -> str:
    """Re-escape bare ``"`` inside ``"content": "..."`` values.

    ClickUp's markdown renderer strips the ``\\`` from ``\\\"`` inside code
    blocks, turning valid JSON escapes into bare quotes that break
    ``json.loads``.  Because the formatted JSON has each content value on
    a **single line**, we can safely identify and repair them.
    """
    _CONTENT_LINE_RE = re.compile(
        r'^(\s*"content"\s*:\s*")'   # prefix: key + opening quote
        r'(.*)'                      # body (greedy)
        r'("\s*,?\s*)$',             # closing quote + optional comma
        re.MULTILINE,
    )

    def _escape_body(m: re.Match) -> str:
        prefix = m.group(1)
        body = m.group(2)
        suffix = m.group(3)
        # Escape any " that isn't already preceded by a backslash
        fixed = re.sub(r'(?<!\\)"', '\\"', body)
        return prefix + fixed + suffix

    return _CONTENT_LINE_RE.sub(_escape_body, text)


def _try_json_loads(text: str) -> dict[str, Any] | None:
    """Try ``json.loads`` on *text* with increasingly aggressive fixes.

    ClickUp's markdown description:
    1. May double backslashes (``\\\\n`` instead of ``\\n``).
    2. May strip quote escaping, turning escaped quotes into bare ones
       (e.g. triple-quoted ABAP comments become unescaped).

    We attempt parsing in order of least to most transformation.
    Returns the parsed dict or ``None``.
    """
    candidates: list[str] = [
        text,                                      # 1. as-is
        text.replace("\\\\", "\\"),                 # 2. halve backslashes
        _fix_content_quotes(text),                 # 3. re-escape content quotes
        _fix_content_quotes(text.replace("\\\\", "\\")),  # 4. both fixes
    ]

    for i, candidate in enumerate(candidates):
        try:
            data = json.loads(candidate)
            if isinstance(data, dict):
                if i > 0:
                    logger.info("JSON parsed on attempt %d (with ClickUp escaping fixes)", i + 1)
                return data
        except (json.JSONDecodeError, ValueError):
            continue

    return None


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
    return _try_json_loads(candidate)


def _normalize_files(files: Any) -> dict[str, str] | None:
    """Accept *files* as either a dict or an array-of-objects and return a dict.

    Supported formats:
    - ``{"path": "content", ...}``  (dict - original format)
    - ``[{"path": "...", "content": "..."}, ...]``  (array - superagent format)

    Returns ``None`` if the format is unrecognised.
    """
    if isinstance(files, dict):
        return files

    if isinstance(files, list):
        result: dict[str, str] = {}
        for entry in files:
            if isinstance(entry, dict) and "path" in entry and "content" in entry:
                result[entry["path"]] = entry["content"]
            else:
                logger.warning("Skipping invalid files array entry: %s", entry)
        if result:
            return result

    return None


def _unescape_file_contents(files: dict[str, str]) -> dict[str, str]:
    """Replace literal ``\\n`` and ``\\t`` sequences with real whitespace."""
    out: dict[str, str] = {}
    for path, content in files.items():
        # Only unescape if the content has literal \n but no real newlines
        # (indicates double-escaping by the superagent)
        if "\\n" in content and "\n" not in content:
            content = content.replace("\\n", "\n").replace("\\t", "\t")
        out[path] = content
    return out


def _validate_payload(data: dict[str, Any]) -> dict[str, Any] | None:
    """Return *data* if it looks like a valid code-push payload, else None."""
    if not isinstance(data, dict):
        logger.warning("JSON payload is not a dict – ignoring")
        return None
    if "files" not in data:
        logger.warning("JSON payload has no 'files' field – ignoring")
        return None

    normalized = _normalize_files(data["files"])
    if normalized is None:
        logger.warning("JSON payload 'files' is neither a dict nor a valid array – ignoring")
        return None

    # Unescape double-escaped content and store back as a dict
    data["files"] = _unescape_file_contents(normalized)
    return data


def parse_json_payload(description: str) -> dict[str, Any] | None:
    """Extract a JSON payload from *description* and return it as a dict.

    Tries fenced code blocks first, then falls back to raw JSON detection.

    Returns ``None`` if no valid JSON payload is found.  The returned dict
    may contain any of the ``PushCodeRequest`` fields (``github_repo``,
    ``github_branch``, ``base_branch``, ``files``, ``commit_message``,
    ``create_pr``, ``pr_target_branch``).
    """
    # --- Attempt 1: fenced ``` ... ``` block (any language tag) -----------
    match = _JSON_BLOCK_RE.search(description)
    if match:
        raw = match.group(1).strip()
        data = _try_json_loads(raw)
        if data is None:
            logger.warning("Found a fenced code block but could not parse as JSON")
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
