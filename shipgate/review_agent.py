"""Review agent — diff hygiene and change-risk analysis.

Looks at the shape of the change the way a human reviewer would before
reading a single line closely:

* overall size (churn) and file count — big changes hide bugs;
* debug statements left in added code (``print()``, ``console.log()``,
  ``pdb``/``breakpoint()``, JS ``debugger``);
* TODO / FIXME / HACK markers introduced by the change;
* very large functions added in one go (approximate, diff-only heuristic).
"""
from __future__ import annotations

import re

from .diffutil import FileChange
from .findings import AgentResult

AGENT = "review"

_DEBUG_PATTERNS = [
    (re.compile(r"^\s*print\s*\("), "Python print() left in added code"),
    (re.compile(r"console\.log\s*\("), "console.log() left in added code"),
    (re.compile(r"\b(?:pdb\.set_trace|breakpoint)\s*\("), "debugger breakpoint left in added code"),
    (re.compile(r"^\s*debugger\s*;?\s*$"), "JS debugger statement left in added code"),
    (re.compile(r"\bfmt\.Print(?:ln|f)?\s*\("), "Go fmt.Print* left in added code"),
]
_TODO_RE = re.compile(r"\b(TODO|FIXME|HACK|XXX)\b")
_DEF_RE = re.compile(r"^(\s*)(?:def|func|function)\s+([A-Za-z_][\w]*)")

# Thresholds (lines of churn) for size findings.
CHURN_MEDIUM = 150
CHURN_HIGH = 400
MANY_FILES = 15
LARGE_FUNCTION_LINES = 40


def _large_added_functions(change: FileChange) -> list[tuple[str, int, int]]:
    """Approximate sizes of functions added in *change*.

    Diff-only heuristic: starting from an added ``def`` line, count the
    following added lines that are indented deeper, stopping at the first
    line that dedents back. Hunk boundaries can truncate a body, so sizes
    are a lower bound — good enough to flag "this function arrived huge".
    Returns (function_name, start_line, approx_body_lines).
    """
    results: list[tuple[str, int, int]] = []
    lines = change.added
    for i, (lineno, text) in enumerate(lines):
        m = _DEF_RE.match(text)
        if not m:
            continue
        base_indent = len(m.group(1))
        body = 0
        for _, following in lines[i + 1:]:
            stripped = following.strip()
            if not stripped:
                continue
            indent = len(following) - len(following.lstrip())
            if indent <= base_indent:
                break
            body += 1
        if body >= LARGE_FUNCTION_LINES:
            results.append((m.group(2), lineno, body))
    return results


def run(changes: list[FileChange]) -> AgentResult:
    result = AgentResult(AGENT)
    total_churn = sum(c.churn for c in changes)
    total_added = sum(len(c.added) for c in changes)
    total_removed = sum(len(c.removed) for c in changes)
    result.metrics = {
        "files_changed": len(changes),
        "lines_added": total_added,
        "lines_removed": total_removed,
        "churn": total_churn,
    }

    if total_churn >= CHURN_HIGH:
        result.add("high",
                   f"Very large change: {total_churn} lines of churn across "
                   f"{len(changes)} files. Large changes are hard to review "
                   "well — consider splitting the merge request.")
    elif total_churn >= CHURN_MEDIUM:
        result.add("medium",
                   f"Large change: {total_churn} lines of churn. Review "
                   "carefully or split into smaller merge requests.")

    if len(changes) > MANY_FILES:
        result.add("medium",
                   f"Change touches {len(changes)} files (threshold "
                   f"{MANY_FILES}); wide blast radius.")

    for change in changes:
        for lineno, text in change.added:
            for pattern, label in _DEBUG_PATTERNS:
                if pattern.search(text):
                    result.add("medium", f"{label}: {text.strip()[:80]}",
                               change.path, lineno)
                    break
            todo = _TODO_RE.search(text)
            if todo:
                result.add("low",
                           f"{todo.group(1)} marker introduced: {text.strip()[:80]}",
                           change.path, lineno)
        for name, lineno, size in _large_added_functions(change):
            result.add("medium",
                       f"Large function '{name}' added (~{size} body lines in "
                       "the diff). Consider breaking it up before merge.",
                       change.path, lineno)

    if not result.findings:
        result.summary = (
            f"Change is review-friendly: {total_churn} lines of churn in "
            f"{len(changes)} file(s), no debug statements, TODOs or oversized "
            "functions introduced."
        )
    else:
        result.summary = (
            f"{len(result.findings)} review concern(s) in a change of "
            f"{total_churn} churn lines across {len(changes)} file(s)."
        )
    return result
