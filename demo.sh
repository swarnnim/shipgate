#!/usr/bin/env bash
# ShipGate end-to-end demo.
#
# Runs the full post-code gauntlet on two sample changes against the
# sample app in samples/app/:
#   1. clean_change.diff — a well-formed change (code + tests + changelog)
#      Expected verdict: APPROVE (exit code 0)
#   2. dirty_change.diff — a change with a planted secret, a forbidden
#      .env file, SQL-injection-shaped code, eval(), a debug print, a
#      TODO, no tests and no changelog entry
#      Expected verdict: BLOCK (exit code 1)
#
# The planted "Stripe key" is Stripe's public documentation example key
# (sk_live_4eC39HqLyjWDarjtT1zdp7dc) — a canonical fake, not a real secret.
#
# Usage: ./demo.sh        (from anywhere; it cds to the project root)
set -u
cd "$(dirname "$0")"
mkdir -p build

PY="$(command -v python3 || command -v python)"

echo "==================================================================="
echo "ShipGate demo — case 1: clean change"
echo "==================================================================="
"$PY" -m shipgate --diff samples/clean_change.diff --repo samples/app \
    --out-md build/clean-report.md --out-json build/clean-verdict.json
clean_code=$?
echo "exit code: $clean_code (expected 0)"

echo
echo "==================================================================="
echo "ShipGate demo — case 2: dirty change (planted secret + no tests)"
echo "==================================================================="
"$PY" -m shipgate --diff samples/dirty_change.diff --repo samples/app \
    --out-md build/dirty-report.md --out-json build/dirty-verdict.json
dirty_code=$?
echo "exit code: $dirty_code (expected 1)"

echo
clean_verdict="$("$PY" -c "import json;print(json.load(open('build/clean-verdict.json'))['verdict'])")"
dirty_verdict="$("$PY" -c "import json;print(json.load(open('build/dirty-verdict.json'))['verdict'])")"
echo "Clean change verdict: $clean_verdict"
echo "Dirty change verdict: $dirty_verdict"
echo "Full reports: build/clean-report.md and build/dirty-report.md"

if [ "$clean_code" -eq 0 ] && [ "$clean_verdict" = "APPROVE" ] \
   && [ "$dirty_code" -eq 1 ] && [ "$dirty_verdict" = "BLOCK" ]; then
    echo "DEMO PASSED: clean change approved, dirty change blocked."
    exit 0
fi
echo "DEMO FAILED: unexpected verdicts (see reports above)."
exit 1
