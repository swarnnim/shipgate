# ShipGate — the post-code DevSecOps gate

**Hackathon:** GitLab Transcend Hackathon ("Life After Code") · **Track fit:** thorough GitLab automation of the post-code lifecycle + sustainability (SCI) quantification.

> Code generation is solved. What happens next — review, security scanning,
> test verification, compliance, release gating — is still mostly humans
> clicking through checklists. ShipGate is an autonomous agent that runs
> that whole gauntlet on a merge request and issues a release-readiness
> verdict: **APPROVE**, **APPROVE_WITH_WARNINGS**, or **BLOCK**.

ShipGate is a single pure-Python-3 package (standard library only — no
`pip install`, no network calls, no credentials, nothing to phone home).
It reads a unified diff, optionally cross-references the repository tree,
and produces:

1. a **Markdown report** you can read in the GitLab pipeline artifacts
   (and paste into an MR comment),
2. a **JSON verdict** for machines, and
3. a **process exit code** (0 / 2 / 1) so a pipeline can actually gate on it.

---

## The problem

After code is written, every merge request needs the same five questions
answered: *Is this change reviewable? Does it leak secrets or introduce
dangerous patterns? Is the new code tested? Does it break our compliance
rules? What does shipping it cost the planet?* In most teams those checks
are manual, inconsistent, and happen late — which is how live API keys end
up in git history and untested payment code reaches production.

ShipGate answers all five questions automatically, in seconds, on every
merge request, using GitLab CI as its runtime.

## How it works

```
            unified diff (+ repo tree)
                      │
        ┌─────────────┼─────────────────────────────┐
        ▼             ▼              ▼                ▼             ▼
   ┌─────────┐  ┌───────────┐  ┌───────────┐  ┌────────────┐  ┌──────────────┐
   │ Review  │  │ Security  │  │ Test-gap  │  │ Compliance │  │ Sustainability│
   │ agent   │  │ agent     │  │ agent     │  │ agent      │  │ (SCI)        │
   └────┬────┘  └─────┬─────┘  └─────┬─────┘  └──────┬─────┘  └──────┬───────┘
        └─────────────┴──────┬───────┴───────────────┘               │
                             ▼                                       │
                    Orchestrator: weighted score,                  │
                    hard-block rules, verdict ◄────────────────────┘
                             │
              ┌──────────────┼───────────────┐
              ▼              ▼               ▼
       Markdown report   JSON verdict    exit code
```

### The agents

| Agent | Weight | What it checks |
|---|---|---|
| **Security** | 35% | Secret formats in added lines (AWS keys, GitHub/Stripe/Slack tokens, private-key blocks, JWTs, hard-coded credential assignments — with a placeholder filter so `password = "changeme"` fixtures stay quiet) and dangerous patterns: `eval`/`exec`, `shell=True`, SQL built by string formatting, `pickle.loads`, `yaml.load` without SafeLoader, disabled TLS verification, MD5/SHA1. |
| **Review** | 20% | Change size and blast radius (churn / file-count thresholds), debug statements left in (`print`, `console.log`, `pdb`, `debugger`), TODO/FIXME/HACK markers introduced, and functions added already-large (diff-only size heuristic). |
| **Test-gap** | 20% | Maps every changed source file to its conventional test file (`src/foo.py` → `tests/test_foo.py`, `foo.test.js`, `foo_test.go`, …) across the repo tree *and* tests added in the same diff; then checks that every added/changed function is referenced by some test. |
| **Compliance** | 15% | Forbidden credential files (`.env`, `*.pem`, `id_rsa`, `credentials*`, … — templates like `.env.example` are allowed) as **critical** findings; license/SPDX headers on changed source files; changelog discipline (source changed ⇒ `CHANGELOG.md` updated). |
| **Sustainability** | 10% | Computes the SCI (below) of running this gate automatically vs. doing the same work manually; sub-score is the saving percentage. It informs, it never blocks. |

### Scoring and verdicts

Each agent starts at 100 and loses points per finding
(info 0 · low 3 · medium 10 · high 25 · critical 60). The orchestrator
combines sub-scores with the weights above, then applies hard rules:

* any **critical** finding, or a weighted score < 50 → **BLOCK** (exit 1)
* weighted score < 85, or any **high** finding → **APPROVE_WITH_WARNINGS** (exit 2)
* otherwise → **APPROVE** (exit 0)

The rules are deliberately simple enough to print on one screen and audit.

## Quick start

### Run the demo (60 seconds)

```bash
./demo.sh
```

This runs the full flow on two sample changes against the sample app in
`samples/app/`:

| Sample | Contents | Verdict |
|---|---|---|
| `samples/clean_change.diff` | `multiply()` added to `src/calculator.py`, **with** its test and a changelog entry | ✅ **APPROVE** (score 99.8, exit 0) |
| `samples/dirty_change.diff` | new `src/payments.py` with a planted secret key + hard-coded DB password, SQL string formatting, `eval()`, a debug `print`, a TODO, **no tests**, plus a committed `.env` file | 🛑 **BLOCK** (score 45.2, exit 1) |

> The planted key is `sk_live_4eC39HqLyjWDarjtT1zdp7dc` — Stripe's public
> *documentation example* key, a canonical fake. No real credential appears
> anywhere in this project.

Reports land in `build/clean-report.md` and `build/dirty-report.md`.

### Run it on your own change

```bash
git diff origin/main...HEAD > mr.diff
python3 -m shipgate --diff mr.diff --repo . \
    --out-md shipgate-report.md --out-json shipgate-verdict.json
echo "exit code: $?"   # 0 approve · 2 approve-with-warnings · 1 block
```

…or pipe a diff straight in: `git diff HEAD | python3 -m shipgate --diff -`.

Useful flags: `--grid-intensity <gCO2e/kWh>` (SCI, see below),
`--mrs-per-month <n>` (annualised saving), `--quiet`.

### Run the tests

```bash
python3 run_tests.py        # stdlib-only runner — 26 tests
python3 -m pytest tests/    # the same suite under pytest, if you have it
```

## GitLab integration

`.gitlab-ci.yml` (repo root) is the whole integration — no CI/CD variables
or secrets needed:

* A `shipgate` job in a `gate` stage runs on **merge request pipelines**
  only, computes the three-dot diff against the MR's target branch, and
  runs ShipGate against the checked-out repo.
* Exit-code semantics: BLOCK (1) fails the job — with GitLab's
  *"Pipelines must succeed"* merge setting, that blocks the merge.
  APPROVE_WITH_WARNINGS (2) is translated to a successful job with a loud
  log line. APPROVE (0) passes silently.
* `shipgate-report.md` and `shipgate-verdict.json` are uploaded as
  artifacts **on every run** (`when: always`, 30-day expiry) — reviewers
  read the report from the job page without re-running anything.
* **Advisory mode:** add `allow_failure: true` to report without ever
  blocking a merge (documented in the YAML comments).

Because ShipGate is stdlib-only and offline, the job needs nothing but a
`python:3.12-slim` image — no package installs, no external services, no
tokens. It also drops into any other CI system that can run Python and
read an exit code.

## Sustainability (SCI)

We quantify the sustainability gain of automating the post-code lifecycle
using the **Software Carbon Intensity (SCI)** specification from the Green
Software Foundation (v1.0, adopted as ISO/IEC 21031:2024):

```
SCI = ((E × I) + M) / R
```

with the functional unit **R = 1 merge request** taken from "code written"
to "release-ready verdict".

| Symbol | Meaning | ShipGate's value (all overridable) |
|---|---|---|
| E | energy | automated: 4 min CI job × 12 W effective runner share × PUE 1.2 · manual: 40 min of engineer time × 95 W workstation (laptop + monitor) |
| I | grid carbon intensity | 475 gCO2e/kWh default — approximate global average (Ember Global Electricity Review 2024, via Our World in Data); set `--grid-intensity` for your region |
| M | embodied emissions, amortised | server ≈ 1,500 kgCO2e / 4 years, 1/16 host share for the job · laptop ≈ 190 kgCO2e / 4 working years (mid-range of published manufacturer LCAs) |
| Runs | feedback-loop factor | manual process re-runs CI ≈ 2.0× per MR (issues found late); automated ≈ 1.15× |

**Result with the default constants:**

| Process | SCI (gCO2e per MR) |
|---|---|
| ShipGate automated gate | **0.729** |
| Manual post-code process | **48.018** |

→ **47.3 gCO2e saved per MR (98.5%)**, ≈ **113.5 kgCO2e/year** for a team
merging 200 MRs/month. Every constant lives in `shipgate/sustainability.py`
with its source and rationale, and the full breakdown (E, operational vs.
embodied split, run counts) is in the JSON verdict.

*Honesty note:* the saving is dominated by human workstation time — that
is the true shape of the comparison (the functional unit is the whole
post-code process, not just compute), but it is sensitive to the 40-minute
manual-review assumption. At a brisk 10 minutes of manual work the saving
is still ≈ 94%; the module's job is to make the trade-off visible and
auditable, not to inflate it. These are order-of-magnitude estimates from
published constants, not meter readings.

## Project layout

```
shipgate/
  cli.py              argument parsing, exit codes
  orchestrator.py     weights, verdict rules, Markdown/JSON rendering
  diffutil.py         lenient unified-diff parser
  findings.py         Finding / AgentResult model + scoring deductions
  review_agent.py     size, debug leftovers, TODOs, oversized functions
  security_agent.py   secret formats + dangerous-pattern scan
  testgap_agent.py    source→test mapping, untested-function detection
  compliance_agent.py forbidden files, license headers, changelog rule
  sustainability.py   SCI model: constants, sources, estimate()
samples/
  app/                tiny demo target repo (calculator + tests + changelog)
  clean_change.diff   the good change  → APPROVE
  dirty_change.diff   the bad change   → BLOCK
tests/test_shipgate.py  26 tests (diff parsing, every agent, SCI math, verdicts)
run_tests.py          stdlib-only test runner
demo.sh               end-to-end demo; asserts both expected verdicts
.gitlab-ci.yml        the GitLab pipeline described above
```

## Limitations (honest list)

* **Heuristic scanner, not a SAST suite.** Regex-based secret/pattern
  detection catches the common, high-confidence shapes. It will miss
  obfuscated secrets and novel vulnerability classes, and can false-positive
  on unusual-but-safe code. Treat it as a fast first gate alongside — not
  instead of — tools like GitLab SAST/Secret Detection and human review.
* **Diff-only view.** Agents see added lines and (with `--repo`) file
  names/headers/test contents. They do not build the project, run the
  tests, or do data-flow analysis. "Function is referenced by a test" is
  a name-level check, not proof the test exercises it well.
* **Large-function sizing is approximate** (hunk boundaries truncate
  bodies), and churn thresholds are one-size defaults.
* **SCI numbers are estimates** from documented constants, sensitive to
  the manual-baseline assumptions (see the sustainability section).
* Patterns are tuned for Python/JS/Go-flavoured codebases; other
  ecosystems get the generic subset (secrets, forbidden files, changelog).

## License

MIT — see the SPDX headers in the source files. Demo fixture secrets are
canonical public examples (Stripe/AWS documentation), not real credentials.
