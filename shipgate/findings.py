"""Shared data model: findings and per-agent results.

Every agent returns an :class:`AgentResult` containing zero or more
:class:`Finding` objects and a 0-100 sub-score. Scores start at 100 and
lose points per finding according to :data:`SEVERITY_DEDUCTIONS`, which
keeps the scoring rule identical across agents and easy to audit.
"""
from __future__ import annotations

from dataclasses import dataclass, field

SEVERITIES = ("info", "low", "medium", "high", "critical")

# Points deducted from an agent's 100-point sub-score per finding.
SEVERITY_DEDUCTIONS = {
    "info": 0,
    "low": 3,
    "medium": 10,
    "high": 25,
    "critical": 60,
}


@dataclass
class Finding:
    """One thing an agent noticed about the change."""

    agent: str
    severity: str  # one of SEVERITIES
    message: str
    path: str | None = None
    line: int | None = None

    def __post_init__(self) -> None:
        if self.severity not in SEVERITIES:
            raise ValueError(f"unknown severity: {self.severity!r}")

    def to_dict(self) -> dict:
        return {
            "agent": self.agent,
            "severity": self.severity,
            "message": self.message,
            "path": self.path,
            "line": self.line,
        }


@dataclass
class AgentResult:
    """The outcome of one agent's pass over the change."""

    name: str
    findings: list[Finding] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    summary: str = ""
    # Agents whose score is not deduction-based (e.g. sustainability) set
    # this; when set, it replaces the deduction-derived score everywhere.
    score_override: float | None = None

    @property
    def score(self) -> float:
        """100 minus severity deductions, clamped to [0, 100]."""
        if self.score_override is not None:
            return max(0.0, min(100.0, self.score_override))
        deducted = sum(SEVERITY_DEDUCTIONS[f.severity] for f in self.findings)
        return max(0.0, 100.0 - deducted)

    @property
    def worst_severity(self) -> str | None:
        if not self.findings:
            return None
        return max(self.findings, key=lambda f: SEVERITIES.index(f.severity)).severity

    def add(self, severity: str, message: str, path: str | None = None,
            line: int | None = None) -> None:
        self.findings.append(Finding(self.name, severity, message, path, line))

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "score": round(self.score, 1),
            "worst_severity": self.worst_severity,
            "summary": self.summary,
            "metrics": self.metrics,
            "findings": [f.to_dict() for f in self.findings],
        }
