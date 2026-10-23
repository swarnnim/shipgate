"""Security agent — secret detection and vulnerability-pattern scan.

Two passes over the *added* lines of the diff (removed lines cannot
introduce new risk into the codebase):

1. **Secrets** — high-precision regexes for well-known token formats
   (AWS, GitHub, Stripe, Slack, private keys, JWTs) plus a generic
   hard-coded credential-assignment pattern with a placeholder filter so
   ``password = "changeme"`` in a fixture does not page anyone.
2. **Dangerous code patterns** — ``eval``/``exec``, ``shell=True``,
   SQL built with string formatting, unsafe deserialisation, disabled
   TLS verification, weak hashes.

This is a fast local heuristic scanner, not a replacement for a full
SAST tool — the README says so. Secrets are *critical*: one leaked key
blocks the release.
"""
from __future__ import annotations

import re

from .diffutil import FileChange
from .findings import AgentResult

AGENT = "security"

# (label, pattern, severity) — at most one secret finding per line.
SECRET_PATTERNS: list[tuple[str, re.Pattern, str]] = [
    ("AWS access key ID", re.compile(r"AKIA[0-9A-Z]{16}"), "critical"),
    ("GitHub token", re.compile(r"(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{20,})"), "critical"),
    ("Stripe live secret key", re.compile(r"sk_live_[A-Za-z0-9]{16,}"), "critical"),
    ("Slack token", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"), "critical"),
    ("Private key block", re.compile(r"BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY"), "critical"),
    ("JSON Web Token", re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"), "high"),
    (
        # Identifier *contains* a credential keyword (DB_PASSWORD, API_TOKEN,
        # app_secret, ...) and is assigned a quoted literal of 8+ chars.
        "Hard-coded credential assignment",
        re.compile(r"(?i)[A-Za-z_][\w-]*(?:password|passwd|api[_-]?key|secret"
                   r"|access[_-]?token|auth[_-]?token|token)[\w-]*"
                   r"\s*[:=]\s*[\"'][^\"'\n]{8,}[\"']"),
        "critical",
    ),
]

# Values that are obviously placeholders, not real credentials.
_PLACEHOLDER_RE = re.compile(
    r"(?i)example|changeme|change-me|placeholder|your[_-]|xxxx|dummy|"
    r"redacted|\$\{|getenv|environ|os\.environ|<[^>]+>"
)

# (label, pattern, severity, explanation)
VULN_PATTERNS: list[tuple[str, re.Pattern, str, str]] = [
    ("eval() call", re.compile(r"\beval\s*\("), "high",
     "eval() executes arbitrary code; if any input reaches it, that is remote code execution."),
    ("exec() call", re.compile(r"\bexec\s*\("), "high",
     "exec() executes arbitrary code; prefer explicit dispatch tables."),
    ("shell=True", re.compile(r"shell\s*=\s*True"), "high",
     "subprocess with shell=True invites command injection when arguments contain input."),
    ("os.system() call", re.compile(r"\bos\.system\s*\("), "medium",
     "os.system() runs a shell command string; use subprocess with an argument list."),
    ("pickle deserialisation", re.compile(r"\bpickle\.loads?\s*\("), "medium",
     "Unpickling untrusted data executes arbitrary code during deserialisation."),
    ("TLS verification disabled", re.compile(r"verify\s*=\s*False"), "medium",
     "verify=False disables TLS certificate checking and enables MITM attacks."),
    ("Weak hash (MD5/SHA1)", re.compile(r"hashlib\.(?:md5|sha1)\b"), "low",
     "MD5/SHA1 are collision-broken; never use them for passwords or integrity."),
]

_SQL_RE = re.compile(r"(?i)\b(?:SELECT|INSERT\s+INTO|UPDATE|DELETE\s+FROM)\b")


def _looks_like_sql_formatting(text: str) -> bool:
    if not _SQL_RE.search(text):
        return False
    if ".format(" in text or "%" in text:
        return True
    # f-string with an SQL keyword: f"SELECT ... {x}"
    stripped = text.lstrip()
    return stripped.startswith(("f\"", "f'")) and "{" in text


def run(changes: list[FileChange]) -> AgentResult:
    result = AgentResult(AGENT)
    secrets_found = 0
    vulns_found = 0

    for change in changes:
        for lineno, text in change.added:
            # --- secrets: one finding per line, first matching pattern ---
            for label, pattern, severity in SECRET_PATTERNS:
                m = pattern.search(text)
                if not m:
                    continue
                if label == "Hard-coded credential assignment" and _PLACEHOLDER_RE.search(text):
                    break  # placeholder value, not a real credential
                result.add(severity,
                           f"Possible {label} committed in added code. Rotate it "
                           "if real, and move it to a secret store / CI variable.",
                           change.path, lineno)
                secrets_found += 1
                break

            # --- dangerous patterns ---
            for label, pattern, severity, why in VULN_PATTERNS:
                if pattern.search(text):
                    result.add(severity, f"{label}: {why}",
                               change.path, lineno)
                    vulns_found += 1
            if "yaml.load(" in text and "SafeLoader" not in text:
                result.add("medium",
                           "yaml.load() without SafeLoader can construct "
                           "arbitrary Python objects from untrusted YAML.",
                           change.path, lineno)
                vulns_found += 1
            if _looks_like_sql_formatting(text):
                result.add("high",
                           "SQL query built with string formatting — classic "
                           "SQL-injection shape. Use parameterised queries.",
                           change.path, lineno)
                vulns_found += 1

    result.metrics = {"secrets_found": secrets_found, "vuln_patterns_found": vulns_found}
    if not result.findings:
        result.summary = "No secrets or dangerous code patterns found in added lines."
    else:
        result.summary = (
            f"{secrets_found} possible secret(s) and {vulns_found} dangerous "
            "pattern(s) in added lines."
        )
    return result
