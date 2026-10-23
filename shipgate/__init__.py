"""ShipGate — an autonomous post-merge DevSecOps gate.

ShipGate takes a unified diff (plus, optionally, the repository tree it
applies to) and runs the post-code gauntlet that normally happens by hand
after code is written: review hygiene, security scanning, test-gap
analysis, compliance checks and a sustainability (SCI) estimate. It emits
a Markdown merge-request report, a machine-readable JSON verdict and a
process exit code, so it can gate a GitLab CI pipeline.

Pure Python 3 standard library. No network calls. No credentials.
"""

__version__ = "1.0.0"

__all__ = ["__version__"]
