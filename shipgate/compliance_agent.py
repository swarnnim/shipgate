"""Compliance agent — the boring checks that fail audits.

* **Forbidden files** — credential-shaped files must never enter the repo:
  ``.env`` (templates like ``.env.example`` are fine), private keys,
  credential stores. These are *critical*.
* **License headers** — changed source files should carry an SPDX /
  copyright header. Checked against the repo copy of the file when a repo
  is provided, otherwise against the added lines of new files.
* **Changelog discipline** — if source code changed, ``CHANGELOG.md``
  should change too.
"""
from __future__ import annotations

import fnmatch
import os

from .diffutil import FileChange
from .findings import AgentResult
from .testgap_agent import is_source_file, is_test_path

AGENT = "compliance"

FORBIDDEN_PATTERNS = [
    ".env", ".env.*",
    "credentials", "credentials.*",
    "*.pem", "*.key", "*.p12", "*.pfx", "*.keystore",
    "id_rsa", "id_rsa.*", "id_ed25519", "id_ed25519.*",
    "secrets.json", "secrets.yaml", "secrets.yml", "secrets.toml",
]
# Files that *look* forbidden but are documentation/templates.
ALLOWED_LOOKALIKES = {".env.example", ".env.sample", ".env.template", ".env.dist"}

HEADER_MARKERS = (
    "spdx-license-identifier", "copyright", "licensed under",
    "mit license", "apache license", "gnu general public",
)
HEADER_EXTS = {".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".java", ".c",
               ".h", ".cpp", ".rb", ".php", ".rs", ".sh", ".kt", ".scala"}
CHANGELOG_NAMES = {"changelog.md", "changelog", "changes.md", "history.md", "news.md"}


def _is_forbidden(path: str) -> bool:
    base = os.path.basename(path)
    if base in ALLOWED_LOOKALIKES:
        return False
    return any(fnmatch.fnmatchcase(base, pat) for pat in FORBIDDEN_PATTERNS)


def _has_header(text: str) -> bool:
    head = "\n".join(text.splitlines()[:8]).lower()
    return any(marker in head for marker in HEADER_MARKERS)


def _repo_head(repo_root: str | None, rel: str) -> str | None:
    if not repo_root:
        return None
    try:
        with open(os.path.join(repo_root, rel), encoding="utf-8", errors="replace") as fh:
            return "".join(fh.readline() for _ in range(8))
    except OSError:
        return None


def run(changes: list[FileChange], repo_root: str | None = None) -> AgentResult:
    result = AgentResult(AGENT)

    forbidden = 0
    header_missing = 0
    header_checked = 0
    for change in changes:
        if not change.is_deleted and _is_forbidden(change.path):
            forbidden += 1
            result.add("critical",
                       f"Forbidden file '{change.path}' must not be committed — "
                       "it looks like credentials or environment secrets. "
                       "Remove it and add it to .gitignore.",
                       change.path)

        ext = os.path.splitext(change.path)[1].lower()
        if ext in HEADER_EXTS and not is_test_path(change.path) and not change.is_deleted:
            head = _repo_head(repo_root, change.path)
            if head is None and change.is_new:
                head = "\n".join(change.added_texts[:8])
            if head is not None:
                header_checked += 1
                if not _has_header(head):
                    header_missing += 1
                    result.add("medium",
                               f"'{change.path}' has no license/SPDX header in "
                               "its first lines.",
                               change.path)

    source_changed = any(is_source_file(c.path) for c in changes)
    changelog_changed = any(
        os.path.basename(c.path).lower() in CHANGELOG_NAMES for c in changes
    )
    result.metrics = {
        "forbidden_files": forbidden,
        "license_headers_checked": header_checked,
        "license_headers_missing": header_missing,
        "source_changed": source_changed,
        "changelog_updated": changelog_changed,
    }
    if source_changed and not changelog_changed:
        result.add("medium",
                   "Source code changed but no CHANGELOG.md (or equivalent) "
                   "was updated in this change.")

    if not result.findings:
        result.summary = (
            "No forbidden files, license headers present where checked, and "
            "changelog discipline respected."
            if source_changed else
            "No forbidden files; no source changes requiring changelog updates."
        )
    else:
        result.summary = (
            f"{forbidden} forbidden file(s), {header_missing} missing license "
            "header(s), changelog "
            f"{'updated' if changelog_changed else 'not updated'}."
        )
    return result
