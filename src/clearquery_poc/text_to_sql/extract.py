"""Extract the candidate SQL from raw model output. Trimming only: no repair,
no rewriting, no fallback. Returns None when no SQL can be found."""

from __future__ import annotations

import re

_FENCED = re.compile(r"```(?:sql)?[ \t]*\n(.*?)```", re.IGNORECASE | re.DOTALL)
_STARTS_SQL = re.compile(r"^\s*(SELECT|WITH)\b", re.IGNORECASE)


def extract_sql(raw_output: str | None) -> str | None:
    if not raw_output:
        return None
    # 1. The prompt ends with an opening ```sql fence, so the model normally
    #    continues with SQL and then closes the fence: take the text before
    #    the first ``` if it starts like SQL.
    head = raw_output.split("```", 1)[0]
    if _STARTS_SQL.match(head):
        return head.strip()
    # 2. Otherwise, the first complete fenced block.
    m = _FENCED.search(raw_output)
    if m and _STARTS_SQL.match(m.group(1)):
        return m.group(1).strip()
    return None
