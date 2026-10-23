"""Test-gap agent — which changed code has no test coverage?

For every changed *source* file the agent looks for a corresponding test
file, using both the repository tree (when ``--repo`` is given) and the
files touched by the diff itself — tests added in the same merge request
count. It then extracts the functions the diff adds or changes and checks
whether any test file mentions them.

Mapping conventions (kept intentionally conventional):
  * Python: ``src/foo.py``      -> ``test_foo.py`` / ``foo_test.py``
  * JS/TS:  ``src/foo.js``      -> ``foo.test.js`` / ``foo.spec.ts`` /
                                   ``__tests__/foo.js``
  * Go:     ``foo.go``          -> ``foo_test.go``
  * generic fallback: any test-path file whose stem matches the source stem.
"""
from __future__ import annotations

import os
import re

from .diffutil import FileChange
from .findings import AgentResult

AGENT = "testgap"

SOURCE_EXTS = {".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".java", ".rb",
               ".php", ".c", ".h", ".cpp", ".cs", ".rs", ".kt", ".scala"}
SKIP_PARTS = {".git", "node_modules", "__pycache__", "vendor", "dist", "build"}
TEST_DIR_NAMES = {"test", "tests", "__tests__", "spec", "specs"}
_FUNC_RE = re.compile(
    r"^\s*(?:def|func|function)\s+([A-Za-z_][\w]*)"          # py / go / js
    r"|^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_][\w]*)\s*=\s*(?:async\s*)?\("  # js arrow
)


def is_test_path(path: str) -> bool:
    parts = [p for p in re.split(r"[/\\]", path) if p]
    base = parts[-1] if parts else ""
    stem = base.rsplit(".", 1)[0]
    if any(part in TEST_DIR_NAMES for part in parts[:-1]):
        return True
    return (
        stem.startswith("test_") or stem.endswith("_test")
        or stem.endswith(".test") or stem.endswith(".spec")
        or base.endswith("_test.go")
    )


def is_source_file(path: str) -> bool:
    _, ext = os.path.splitext(path)
    return ext.lower() in SOURCE_EXTS and not is_test_path(path)


def candidate_test_stems(source_path: str) -> set[str]:
    """Stems of test files that would conventionally cover *source_path*."""
    base = os.path.basename(source_path)
    stem, _ = os.path.splitext(base)
    return {f"test_{stem}", f"{stem}_test", f"{stem}.test", f"{stem}.spec", stem}


def index_repo(repo_root: str | None) -> set[str]:
    """All relative file paths in the repo tree (POSIX separators)."""
    found: set[str] = set()
    if not repo_root:
        return found
    for dirpath, dirnames, filenames in os.walk(repo_root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_PARTS]
        for name in filenames:
            rel = os.path.relpath(os.path.join(dirpath, name), repo_root)
            found.add(rel.replace(os.sep, "/"))
    return found


def _read_repo_file(repo_root: str | None, rel: str) -> str:
    if not repo_root:
        return ""
    try:
        with open(os.path.join(repo_root, rel), encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def run(changes: list[FileChange], repo_root: str | None = None) -> AgentResult:
    result = AgentResult(AGENT)
    repo_files = index_repo(repo_root)
    diff_files = {c.path for c in changes}
    universe = repo_files | diff_files
    test_files = {p for p in universe if is_test_path(p)}

    changed_sources = [c for c in changes if is_source_file(c.path)]
    result.metrics = {
        "repo_provided": bool(repo_root),
        "changed_source_files": len(changed_sources),
        "test_files_known": len(test_files),
        "uncovered_files": 0,
        "untested_functions": 0,
    }
    if not changed_sources:
        result.summary = "No source files changed; nothing to map to tests."
        return result

    # Corpus of test text: repo test files + lines added to tests in this diff.
    added_test_text = "\n".join(
        text for c in changes if is_test_path(c.path) for _, text in c.added
    )
    repo_test_text_cache: dict[str, str] = {}

    def test_text_for(stems: set[str]) -> tuple[list[str], str]:
        matched = sorted(
            p for p in test_files
            if os.path.splitext(os.path.basename(p))[0] in stems
        )
        chunks = [added_test_text]
        for path in matched:
            if path not in repo_test_text_cache:
                repo_test_text_cache[path] = _read_repo_file(repo_root, path)
            chunks.append(repo_test_text_cache[path])
        return matched, "\n".join(chunks)

    uncovered = 0
    untested_fns: list[str] = []
    for change in changed_sources:
        stems = candidate_test_stems(change.path)
        matched, corpus = test_text_for(stems)
        if not matched and change.path not in test_files:
            # A same-diff test addition also shows up via diff_files above;
            # reaching here means neither repo nor diff has a test for it.
            if not any(os.path.splitext(os.path.basename(p))[0] in stems
                       for p in diff_files if is_test_path(p)):
                uncovered += 1
                result.add("high",
                           f"No test file found for changed source file "
                           f"'{change.path}'. Add tests before this ships.",
                           change.path)
                continue
        for _, text in change.added:
            m = _FUNC_RE.match(text)
            if not m:
                continue
            fn = m.group(1) or m.group(2)
            if fn and fn not in corpus:
                untested_fns.append(f"{change.path}:{fn}")
                result.add("medium",
                           f"Function '{fn}' changed/added but is not "
                           "referenced by any test file.",
                           change.path)

    result.metrics["uncovered_files"] = uncovered
    result.metrics["untested_functions"] = len(untested_fns)
    if not result.findings:
        result.summary = (
            f"All {len(changed_sources)} changed source file(s) map to test "
            "files, and every changed function is referenced by a test."
        )
    else:
        result.summary = (
            f"{uncovered} changed source file(s) without any test file; "
            f"{len(untested_fns)} changed function(s) unreferenced by tests."
        )
    return result
