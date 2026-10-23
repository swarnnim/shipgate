"""Orchestrator — run every agent, weigh the scores, issue the verdict.

Verdict rules (deliberately simple and auditable):

* any **critical** finding anywhere  -> ``BLOCK``
* weighted score < 50                 -> ``BLOCK``
* weighted score < 85, or any **high** finding -> ``APPROVE_WITH_WARNINGS``
* otherwise                           -> ``APPROVE``

Exit codes (used by the CLI and the GitLab CI job):
``APPROVE`` = 0, ``BLOCK`` = 1, ``APPROVE_WITH_WARNINGS`` = 2.
"""
from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass

from . import __version__, compliance_agent, review_agent, security_agent
from . import sustainability, testgap_agent
from .diffutil import parse_unified_diff
from .findings import AgentResult

WEIGHTS = {
    "security": 0.35,
    "review": 0.20,
    "testgap": 0.20,
    "compliance": 0.15,
    "sustainability": 0.10,
}

VERDICTS = ("APPROVE", "APPROVE_WITH_WARNINGS", "BLOCK")
EXIT_CODES = {"APPROVE": 0, "BLOCK": 1, "APPROVE_WITH_WARNINGS": 2}

_SEVERITY_ICON = {"critical": "🔴", "high": "🟠", "medium": "🟡",
                  "low": "🔵", "info": "⚪"}


@dataclass
class GateReport:
    verdict: str
    score: float
    agents: list[AgentResult]
    sci: dict
    files_changed: int
    generated_at: str

    @property
    def exit_code(self) -> int:
        return EXIT_CODES[self.verdict]

    @property
    def all_findings(self):
        return [f for a in self.agents for f in a.findings]

    @property
    def block_reasons(self) -> list[str]:
        reasons = [
            f"[{f.agent}] {f.message}" + (f" ({f.path})" if f.path else "")
            for f in self.all_findings if f.severity == "critical"
        ]
        if self.verdict == "BLOCK" and not reasons:
            reasons.append(f"Weighted score {self.score:.1f} is below the 50-point floor.")
        return reasons

    def to_dict(self) -> dict:
        return {
            "tool": "ShipGate",
            "tool_version": __version__,
            "generated_at": self.generated_at,
            "verdict": self.verdict,
            "exit_code": self.exit_code,
            "score": round(self.score, 1),
            "weights": WEIGHTS,
            "files_changed": self.files_changed,
            "block_reasons": self.block_reasons,
            "agents": [a.to_dict() for a in self.agents],
            "sustainability": self.sci,
        }

    def to_markdown(self) -> str:
        lines: list[str] = []
        banner = {"APPROVE": "✅ APPROVE", "APPROVE_WITH_WARNINGS": "⚠️ APPROVE WITH WARNINGS",
                  "BLOCK": "🛑 BLOCK"}[self.verdict]
        lines.append("# ShipGate — Release-Readiness Report")
        lines.append("")
        lines.append(f"**Verdict: {banner}** · Weighted score: **{self.score:.1f}/100** · "
                     f"Files changed: {self.files_changed} · Generated: {self.generated_at}")
        lines.append("")
        if self.block_reasons:
            lines.append("## Why this is blocked")
            for reason in self.block_reasons:
                lines.append(f"- {reason}")
            lines.append("")
        lines.append("## Agent scores")
        lines.append("")
        lines.append("| Agent | Weight | Score | Findings | Summary |")
        lines.append("|---|---|---|---|---|")
        for a in self.agents:
            lines.append(f"| {a.name} | {WEIGHTS[a.name]:.0%} | {a.score:.0f} | "
                         f"{len(a.findings)} | {a.summary} |")
        lines.append("")
        for a in self.agents:
            if not a.findings:
                continue
            lines.append(f"## {a.name} — findings")
            lines.append("")
            for f in a.findings:
                loc = f" `{f.path}`" if f.path else ""
                if f.line:
                    loc += f" line {f.line}"
                lines.append(f"- {_SEVERITY_ICON[f.severity]} **{f.severity}**{loc} — {f.message}")
            lines.append("")
        s = self.sci
        sv = s["saving"]
        lines.append("## Sustainability (SCI)")
        lines.append("")
        lines.append(f"Methodology: {s['methodology']} · Grid intensity: "
                     f"{s['grid_intensity_g_per_kwh']:.0f} gCO2e/kWh.")
        lines.append("")
        lines.append("| Process | SCI (gCO2e per MR) |")
        lines.append("|---|---|")
        lines.append(f"| ShipGate automated gate | {s['automated']['sci_g_per_mr']} |")
        lines.append(f"| Manual post-code process | {s['manual_baseline']['sci_g_per_mr']} |")
        lines.append("")
        lines.append(f"**Saving: {sv['g_per_mr']} gCO2e per MR ({sv['pct']}%)**, "
                     f"≈ {sv['kg_per_year_at_volume']} kgCO2e/year at "
                     f"{sv['mrs_per_month_assumed']} MRs/month. Estimates from "
                     "documented constants — see the project README.")
        lines.append("")
        lines.append("---")
        lines.append(f"*ShipGate v{__version__} · heuristic, local, offline analysis. "
                     "Not a substitute for human review or a full SAST/SCA suite.*")
        return "\n".join(lines) + "\n"


def _sustainability_agent(sci: dict) -> AgentResult:
    """Wrap the SCI estimate as an agent result.

    The sustainability score rewards replacing a manual, workstation-bound
    process with a short automated gate: it is the % saving, clamped.
    Sustainability never blocks a release on its own.
    """
    result = AgentResult("sustainability")
    pct = sci["saving"]["pct"]
    auto = sci["automated"]["sci_g_per_mr"]
    manual = sci["manual_baseline"]["sci_g_per_mr"]
    result.metrics = {
        "sci_automated_g_per_mr": auto,
        "sci_manual_g_per_mr": manual,
        "saving_pct": pct,
    }
    if pct < 0:
        result.add("medium",
                   f"Under the current assumptions the automated gate emits "
                   f"more than the manual process ({auto} vs {manual} gCO2e/MR). "
                   "Check the constants in shipgate/sustainability.py.")
    else:
        result.add("info",
                   f"Automated gate SCI ≈ {auto} gCO2e/MR vs manual baseline "
                   f"≈ {manual} gCO2e/MR — a {pct}% saving.")
    result.summary = (f"SCI {auto} gCO2e/MR automated vs {manual} gCO2e/MR "
                      f"manual ({pct}% saving).")
    # This agent's sub-score is the saving percentage itself, not a
    # deduction count, so it uses AgentResult.score_override.
    result.score_override = pct
    return result


def run_gate(diff_text: str, repo_root: str | None = None,
             grid_intensity: float = sustainability.GRID_INTENSITY_DEFAULT,
             mrs_per_month: int = 200) -> GateReport:
    """Run the full gauntlet over *diff_text* and return a GateReport."""
    changes = parse_unified_diff(diff_text)
    sci = sustainability.estimate(grid_intensity=grid_intensity,
                                  mrs_per_month=mrs_per_month)

    sust = _sustainability_agent(sci)
    agents = [
        review_agent.run(changes),
        security_agent.run(changes),
        testgap_agent.run(changes, repo_root),
        compliance_agent.run(changes, repo_root),
        sust,
    ]

    score = sum(WEIGHTS[a.name] * a.score for a in agents)

    findings = [f for a in agents for f in a.findings]
    has_critical = any(f.severity == "critical" for f in findings)
    has_high = any(f.severity == "high" for f in findings)
    if has_critical or score < 50:
        verdict = "BLOCK"
    elif score < 85 or has_high:
        verdict = "APPROVE_WITH_WARNINGS"
    else:
        verdict = "APPROVE"

    return GateReport(
        verdict=verdict,
        score=score,
        agents=agents,
        sci=sci,
        files_changed=len(changes),
        generated_at=_dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
