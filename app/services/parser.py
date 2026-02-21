"""Parse markdown code blocks from a ClickUp task description.

Expected format
---------------
## path/to/file.py
```python
<code here>
```

## another/file.js
```javascript
<code here>
```

The parser looks for ``## <filepath>`` headings immediately followed (after
optional blank lines) by a fenced code block and extracts them into a
``{filepath: content}`` dictionary.
"""

from __future__ import annotations

import re

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
