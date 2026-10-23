"""Unified-diff parsing (stdlib only).

The parser is deliberately lenient: it does not validate hunk line
counts, tolerates bare empty lines as empty context lines, and ignores
metadata it does not need (index lines, mode changes, similarity, ...).
That makes it robust against diffs produced by git, GitLab's web UI,
``diff -u`` and hand-written fixtures alike.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


@dataclass
class FileChange:
    """One file's worth of a unified diff."""

    old_path: str | None = None
    new_path: str | None = None
    # (line_number, text_without_diff_prefix)
    added: list[tuple[int, str]] = field(default_factory=list)
    removed: list[tuple[int, str]] = field(default_factory=list)
    is_binary: bool = False

    @property
    def path(self) -> str:
        return self.new_path or self.old_path or "<unknown>"

    @property
    def is_new(self) -> bool:
        return self.old_path is None and self.new_path is not None

    @property
    def is_deleted(self) -> bool:
        return self.new_path is None and self.old_path is not None

    @property
    def added_texts(self) -> list[str]:
        return [text for _, text in self.added]

    @property
    def removed_texts(self) -> list[str]:
        return [text for _, text in self.removed]

    @property
    def churn(self) -> int:
        return len(self.added) + len(self.removed)


def _clean_path(raw: str) -> str | None:
    """Normalise a ---/+++ path: strip quotes/timestamps and a/ b/ prefixes."""
    p = raw.strip().split("\t")[0].strip()
    if len(p) >= 2 and p[0] == '"' and p[-1] == '"':
        p = p[1:-1]
    if p == "/dev/null":
        return None
    if p.startswith(("a/", "b/")):
        p = p[2:]
    return p


def parse_unified_diff(text: str) -> list[FileChange]:
    """Parse unified diff *text* into a list of :class:`FileChange`."""
    changes: list[FileChange] = []
    current: FileChange | None = None
    old_no = new_no = 0
    in_hunk = False

    for raw in text.splitlines():
        if raw.startswith("diff --git"):
            current = None
            in_hunk = False
            continue
        if raw.startswith("Binary files") and current is not None:
            current.is_binary = True
            in_hunk = False
            continue
        if raw.startswith("--- "):
            current = FileChange(old_path=_clean_path(raw[4:]))
            changes.append(current)
            in_hunk = False
            continue
        if raw.startswith("+++ ") and current is not None:
            current.new_path = _clean_path(raw[4:])
            in_hunk = False
            continue
        m = _HUNK_RE.match(raw)
        if m and current is not None:
            old_no = int(m.group(1))
            new_no = int(m.group(3))
            in_hunk = True
            continue
        if current is not None and in_hunk:
            if raw.startswith("+"):
                current.added.append((new_no, raw[1:]))
                new_no += 1
            elif raw.startswith("-"):
                current.removed.append((old_no, raw[1:]))
                old_no += 1
            elif raw.startswith("\\"):
                continue  # "\ No newline at end of file"
            else:
                # Context line: leading space, or a bare empty line.
                old_no += 1
                new_no += 1
            continue
        # Anything else (index, mode, rename headers, commit text) is ignored.

    return [c for c in changes if c.old_path is not None or c.new_path is not None]
