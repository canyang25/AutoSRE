"""Tests for eval keyword scoring helpers."""

from eval import _keywords_present, evaluate_scenario, print_summary


def test_keywords_present_bigrams():
    text = "The root cause was a database connection pool misconfiguration."
    assert _keywords_present(text, "Database connection pool misconfiguration")


def test_keywords_present_missing_bigram():
    # Words present but not as consecutive bigrams
    text = "pool issues and a separate database outage with connection retries"
    assert not _keywords_present(text, "Database connection pool misconfiguration")


def test_keywords_present_single_word():
    assert _keywords_present("disk space exhausted tonight", "Disk space exhausted")


def test_keywords_present_empty_phrase():
    assert _keywords_present("anything", "a to")  # only filler words


def test_partial_score_with_mock_report(tmp_path, monkeypatch):
    reports = tmp_path / "reports"
    reports.mkdir()
    report = reports / "incident-ALERT-DB-001-20250101T000000Z.md"
    report.write_text(
        "# Report\n\n## Root Cause\nDatabase connection pool misconfiguration\n\n"
        "## Remediation\nunrelated fix\n"
    )

    import eval as eval_mod

    monkeypatch.setattr(eval_mod, "REPORTS_DIR", str(reports))
    monkeypatch.setattr(eval_mod, "run_agent", lambda name: 0)

    result = evaluate_scenario("db", simulate=False)
    assert result["root_cause_match"] is True
    assert result["remediation_match"] is False
    assert result["score"] == 0.5
    assert result["passed"] is False


def test_full_score_with_mock_report(tmp_path, monkeypatch):
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "incident-ALERT-DISK-001-20250101T000000Z.md").write_text(
        "Disk space exhausted. Ran clean_disk_space.yml (free ~15GB of temp files)."
    )

    import eval as eval_mod

    monkeypatch.setattr(eval_mod, "REPORTS_DIR", str(reports))
    monkeypatch.setattr(eval_mod, "run_agent", lambda name: 0)

    result = evaluate_scenario("disk", simulate=False)
    assert result["score"] == 1.0
    assert result["passed"] is True


def test_simulate_mode():
    result = evaluate_scenario("network", simulate=True)
    assert result["passed"] is True
    assert result["score"] == 1.0


def test_print_summary_smoke(capsys):
    print_summary(
        [
            {
                "scenario": "db",
                "root_cause_match": True,
                "remediation_match": False,
                "score": 0.5,
                "passed": False,
            }
        ]
    )
    out = capsys.readouterr().out
    assert "db" in out
    assert "FAIL" in out
