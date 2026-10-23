"""ShipGate command-line interface.

Examples
--------
Analyse a diff file against the repo it applies to::

    python -m shipgate --diff mr.diff --repo . \\
        --out-md shipgate-report.md --out-json shipgate-verdict.json

Pipe a diff in::

    git diff origin/main...HEAD | python -m shipgate --diff -

Exit codes: 0 = APPROVE, 1 = BLOCK, 2 = APPROVE_WITH_WARNINGS.
"""
from __future__ import annotations

import argparse
import json
import sys

from . import orchestrator, sustainability


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="shipgate",
        description="Autonomous post-merge DevSecOps gate: review, security, "
                    "test-gap, compliance and SCI sustainability checks over a "
                    "unified diff.",
    )
    p.add_argument("--diff", required=True,
                   help="Path to a unified diff file, or '-' for stdin.")
    p.add_argument("--repo", default=".",
                   help="Repository root the diff applies to (used for "
                        "test-file mapping and license-header checks). "
                        "Default: current directory.")
    p.add_argument("--out-md", help="Write the Markdown MR report here.")
    p.add_argument("--out-json", help="Write the JSON verdict here.")
    p.add_argument("--grid-intensity", type=float,
                   default=sustainability.GRID_INTENSITY_DEFAULT,
                   help="Grid carbon intensity in gCO2e/kWh for the SCI "
                        "estimate (default: %(default)s, global average).")
    p.add_argument("--mrs-per-month", type=int, default=200,
                   help="Merge-request volume used for the annualised SCI "
                        "saving (default: %(default)s).")
    p.add_argument("--quiet", action="store_true",
                   help="Suppress the human-readable summary on stdout.")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.diff == "-":
        diff_text = sys.stdin.read()
    else:
        with open(args.diff, encoding="utf-8", errors="replace") as fh:
            diff_text = fh.read()

    report = orchestrator.run_gate(
        diff_text,
        repo_root=args.repo,
        grid_intensity=args.grid_intensity,
        mrs_per_month=args.mrs_per_month,
    )

    if args.out_md:
        with open(args.out_md, "w", encoding="utf-8") as fh:
            fh.write(report.to_markdown())
    if args.out_json:
        with open(args.out_json, "w", encoding="utf-8") as fh:
            json.dump(report.to_dict(), fh, indent=2)
            fh.write("\n")

    if not args.quiet:
        print(f"ShipGate verdict: {report.verdict} "
              f"(score {report.score:.1f}/100, "
              f"{report.files_changed} file(s) changed)")
        for reason in report.block_reasons:
            print(f"  BLOCK: {reason}")
        for agent in report.agents:
            print(f"  {agent.name:<15} {agent.summary}")
        if args.out_md:
            print(f"  Markdown report: {args.out_md}")
        if args.out_json:
            print(f"  JSON verdict:    {args.out_json}")

    return report.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
