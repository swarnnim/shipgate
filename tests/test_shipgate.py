"""ShipGate test suite.

Plain-assert test functions: they run under pytest (``python -m pytest
tests/``) or with the stdlib-only runner (``python run_tests.py``).
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
SAMPLES = ROOT / "samples"

from shipgate import compliance_agent, orchestrator, review_agent  # noqa: E402
from shipgate import security_agent, sustainability, testgap_agent  # noqa: E402
from shipgate.diffutil import parse_unified_diff  # noqa: E402
from shipgate.findings import AgentResult  # noqa: E402


def make_diff(path, added, new_file=True):
    if new_file:
        header = (f"diff --git a/{path} b/{path}\n--- /dev/null\n"
                  f"+++ b/{path}\n@@ -0,0 +1,{len(added)} @@\n")
    else:
        header = (f"diff --git a/{path} b/{path}\n--- a/{path}\n"
                  f"+++ b/{path}\n@@ -1,1 +1,{len(added) + 1} @@\n")
    return header + "".join(f"+{line}\n" for line in added)


def read_sample(name):
    return (SAMPLES / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------- diffutil

def test_parse_new_file():
    changes = parse_unified_diff(make_diff("src/x.py", ["a = 1", "b = 2"]))
    assert len(changes) == 1
    c = changes[0]
    assert c.path == "src/x.py" and c.is_new and not c.is_deleted
    assert c.added == [(1, "a = 1"), (2, "b = 2")]
    assert c.churn == 2


def test_parse_modification_line_numbers():
    diff = ("--- a/f.py\n+++ b/f.py\n@@ -1,3 +1,3 @@\n"
            " line1\n-old\n+new\n line3\n")
    (c,) = parse_unified_diff(diff)
    assert c.removed == [(2, "old")]
    assert c.added == [(2, "new")]
    assert not c.is_new


def test_parse_sample_fixtures():
    clean = parse_unified_diff(read_sample("clean_change.diff"))
    assert [c.path for c in clean] == [
        "src/calculator.py", "tests/test_calculator.py", "CHANGELOG.md"]
    dirty = parse_unified_diff(read_sample("dirty_change.diff"))
    assert [c.path for c in dirty] == ["src/payments.py", ".env"]
    assert dirty[0].is_new and len(dirty[0].added) == 15


# ---------------------------------------------------------------- findings

def test_score_deductions():
    r = AgentResult("t")
    assert r.score == 100.0
    r.add("medium", "m")
    assert r.score == 90.0
    r.add("critical", "c")
    assert r.score == 30.0
    r.add("critical", "c2")
    assert r.score == 0.0  # clamped


# ---------------------------------------------------------------- security

def test_security_detects_stripe_key():
    diff = make_diff("src/pay.py", ['KEY = "sk_live_4eC39HqLyjWDarjtT1zdp7dc"'])
    res = security_agent.run(parse_unified_diff(diff))
    assert res.metrics["secrets_found"] == 1
    assert res.findings[0].severity == "critical"


def test_security_detects_password_assignment():
    diff = make_diff("src/db.py", ['DB_PASSWORD = "hunter2-super-secret"'])
    res = security_agent.run(parse_unified_diff(diff))
    assert res.metrics["secrets_found"] == 1


def test_security_detects_aws_and_github_tokens():
    diff = make_diff("src/x.py", [
        'A = "AKIAIOSFODNN7EXAMPLE"',
        'B = "ghp_abcdefghijklmnopqrstuvwxyz0123456789"',
    ])
    res = security_agent.run(parse_unified_diff(diff))
    assert res.metrics["secrets_found"] == 2


def test_security_ignores_placeholders_and_env_lookups():
    diff = make_diff("src/x.py", [
        'API_TOKEN = "example-token-value"',
        'password = os.environ["APP_PASSWORD"]',
        'token = get_token_from_vault()',
    ])
    res = security_agent.run(parse_unified_diff(diff))
    assert res.findings == [], res.findings


def test_security_clean_code_is_quiet():
    diff = make_diff("src/x.py", ["def add(a, b):", "    return a + b"])
    res = security_agent.run(parse_unified_diff(diff))
    assert res.findings == [] and res.score == 100.0


def test_security_flags_dangerous_patterns():
    diff = make_diff("src/x.py", [
        "result = eval(user_input)",
        "subprocess.run(cmd, shell=True)",
        'q = "SELECT * FROM t WHERE id = %s" % uid',
        "data = pickle.loads(blob)",
    ])
    res = security_agent.run(parse_unified_diff(diff))
    labels = " | ".join(f.message for f in res.findings)
    assert "eval()" in labels and "shell=True" in labels
    assert "SQL" in labels and "pickle" in labels
    assert res.metrics["vuln_patterns_found"] == 4


# ---------------------------------------------------------------- review

def test_review_flags_debug_and_todo():
    diff = make_diff("src/x.py", [
        "def f():",
        '    print("debug")',
        "    # TODO: finish this",
    ])
    res = review_agent.run(parse_unified_diff(diff))
    severities = sorted(f.severity for f in res.findings)
    assert severities == ["low", "medium"]


def test_review_flags_very_large_change():
    diff = make_diff("src/big.py", [f"value_{i} = {i}" for i in range(450)])
    res = review_agent.run(parse_unified_diff(diff))
    assert any(f.severity == "high" for f in res.findings)
    assert res.metrics["churn"] == 450


def test_review_clean_fixture_has_no_findings():
    res = review_agent.run(parse_unified_diff(read_sample("clean_change.diff")))
    assert res.findings == [], res.findings


# ---------------------------------------------------------------- testgap

def test_testgap_flags_file_without_tests():
    with tempfile.TemporaryDirectory() as tmp:
        diff = make_diff("src/report.py", ["def build_report():", "    return 1"])
        res = testgap_agent.run(parse_unified_diff(diff), tmp)
        assert res.metrics["uncovered_files"] == 1
        assert any(f.severity == "high" for f in res.findings)


def test_testgap_accepts_covered_change():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "src").mkdir()
        (root / "tests").mkdir()
        (root / "src" / "util.py").write_text("def old():\n    pass\n")
        (root / "tests" / "test_util.py").write_text(
            "from src.util import parse\n\ndef test_parse():\n    parse('x')\n")
        diff = make_diff("src/util.py", ["def parse(data):", "    return data"],
                         new_file=False)
        res = testgap_agent.run(parse_unified_diff(diff), tmp)
        assert res.findings == [], res.findings


def test_testgap_counts_tests_added_in_same_diff():
    diff = (
        make_diff("src/util.py", ["def parse(data):", "    return data"])
        + make_diff("tests/test_util.py",
                    ["def test_parse():", "    assert parse('x') == 'x'"])
    )
    res = testgap_agent.run(parse_unified_diff(diff), None)
    assert res.metrics["uncovered_files"] == 0


# ---------------------------------------------------------------- compliance

def test_compliance_forbidden_env_file():
    diff = make_diff(".env", ["DATABASE_URL=postgres://localhost/db"])
    res = compliance_agent.run(parse_unified_diff(diff))
    assert res.metrics["forbidden_files"] == 1
    assert res.findings[0].severity == "critical"


def test_compliance_env_example_is_allowed():
    diff = make_diff(".env.example", ["DATABASE_URL=postgres://localhost/db"])
    res = compliance_agent.run(parse_unified_diff(diff))
    assert res.metrics["forbidden_files"] == 0


def test_compliance_changelog_rule():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "src").mkdir()
        (root / "src" / "util.py").write_text(
            "# SPDX-License-Identifier: MIT\nx = 1\n")
        src_only = make_diff("src/util.py", ["y = 2"], new_file=False)
        res = compliance_agent.run(parse_unified_diff(src_only), tmp)
        assert any("CHANGELOG" in f.message for f in res.findings)
        with_changelog = src_only + make_diff(
            "CHANGELOG.md", ["- util: tweak"], new_file=False)
        res2 = compliance_agent.run(parse_unified_diff(with_changelog), tmp)
        assert not any("CHANGELOG" in f.message for f in res2.findings)


def test_compliance_license_header_check():
    changelog = make_diff("CHANGELOG.md", ["- x"], new_file=False)
    no_header = make_diff("src/newmod.py", ['"""Module."""', "x = 1"]) + changelog
    res = compliance_agent.run(parse_unified_diff(no_header))
    assert any("license" in f.message.lower() for f in res.findings)
    with_header = make_diff(
        "src/newmod.py", ["# SPDX-License-Identifier: MIT", "x = 1"]) + changelog
    res2 = compliance_agent.run(parse_unified_diff(with_header))
    assert not any("license" in f.message.lower() for f in res2.findings)


# ---------------------------------------------------------------- SCI

def test_sci_formula_math():
    run = sustainability.emissions_for_run(
        duration_min=60, power_w=1000, pue=1.0,
        intensity_g_kwh=100, embodied_g_per_hour=10)
    assert run["energy_kwh"] == 1.0
    assert run["operational_g"] == 100.0
    assert run["embodied_g"] == 10.0
    assert run["total_g"] == 110.0


def test_sci_automated_beats_manual_baseline():
    est = sustainability.estimate()
    auto = est["automated"]["sci_g_per_mr"]
    manual = est["manual_baseline"]["sci_g_per_mr"]
    assert 0 < auto < manual
    assert 0 < est["saving"]["pct"] < 100
    assert est["saving"]["kg_per_year_at_volume"] > 0


def test_sci_scales_with_grid_intensity():
    low = sustainability.estimate(grid_intensity=50)
    high = sustainability.estimate(grid_intensity=900)
    assert high["automated"]["sci_g_per_mr"] > low["automated"]["sci_g_per_mr"]


# ---------------------------------------------------------------- orchestrator

def test_orchestrator_clean_fixture_approves():
    report = orchestrator.run_gate(
        read_sample("clean_change.diff"), repo_root=str(SAMPLES / "app"))
    assert report.verdict == "APPROVE", report.to_dict()
    assert report.exit_code == 0
    assert report.score >= 85


def test_orchestrator_dirty_fixture_blocks():
    report = orchestrator.run_gate(
        read_sample("dirty_change.diff"), repo_root=str(SAMPLES / "app"))
    assert report.verdict == "BLOCK", report.to_dict()
    assert report.exit_code == 1
    assert report.block_reasons
    security = next(a for a in report.agents if a.name == "security")
    assert any(f.severity == "critical" for f in security.findings)


def test_orchestrator_outputs_are_complete():
    report = orchestrator.run_gate(
        read_sample("dirty_change.diff"), repo_root=str(SAMPLES / "app"))
    payload = json.loads(json.dumps(report.to_dict()))  # must serialise
    assert payload["verdict"] == "BLOCK"
    assert {a["name"] for a in payload["agents"]} == {
        "review", "security", "testgap", "compliance", "sustainability"}
    md = report.to_markdown()
    assert "BLOCK" in md and "Sustainability (SCI)" in md
    assert "gCO2e per MR" in md
