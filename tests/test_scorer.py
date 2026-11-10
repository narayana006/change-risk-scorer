import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from change_risk_scorer import cli
from change_risk_scorer.config import ConfigError, load_config, parse_config
from change_risk_scorer.gitdiff import FileChange, GitError, changed_files, parse_numstat
from change_risk_scorer.render import render_json, render_markdown, render_text
from change_risk_scorer.scoring import area_of, level_for, matches, score_change

EXAMPLE_CONFIG = Path(__file__).resolve().parent.parent / "examples" / "change-risk.yml"
WEEKDAY_NOON = datetime(2026, 11, 10, 12, 0)  # a Tuesday


def fc(path, added=1, deleted=0):
    return FileChange(path, added, deleted)


def score(paths, config=None, now=WEEKDAY_NOON):
    files = [p if isinstance(p, FileChange) else fc(p) for p in paths]
    return score_change(files, parse_config(config), now)


def factors(result):
    return {r.factor: r.points for r in result.reasons}


# --- git --------------------------------------------------------------------

def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=T", "-c", "user.email=t@example.com", *args],
                   check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    (r / "app.py").write_text("a = 1\nb = 2\n")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "base")
    git(r, "checkout", "-q", "-b", "feature")
    (r / "app.py").write_text("a = 1\nb = 3\nc = 4\n")
    (r / "db" / "migrations").mkdir(parents=True)
    (r / "db" / "migrations" / "001.sql").write_text("CREATE TABLE t (id INT);\n")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "feature")
    # A later commit on main must not show up in the feature diff.
    git(r, "checkout", "-q", "main")
    (r / "other.txt").write_text("x\n")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "main moves on")
    return r


def test_parse_numstat_handles_binary_and_blank_lines():
    out = "3\t1\tsrc/a.py\n-\t-\tlogo.png\n\n0\t7\tpath with space.txt\n"
    assert parse_numstat(out) == [fc("src/a.py", 3, 1), fc("logo.png", 0, 0), fc("path with space.txt", 0, 7)]


def test_changed_files_uses_merge_base(repo):
    files = changed_files(str(repo), "main", "feature")
    assert sorted(files, key=lambda f: f.path) == [fc("app.py", 2, 1), fc("db/migrations/001.sql", 1, 0)]


def test_changed_files_reports_bad_ref_and_non_repo(repo, tmp_path):
    with pytest.raises(GitError, match="cannot diff nope...feature"):
        changed_files(str(repo), "nope", "feature")
    with pytest.raises(GitError):
        changed_files(str(tmp_path), "main", "feature")


# --- path rules -------------------------------------------------------------

@pytest.mark.parametrize("path, factor, points", [
    ("db/migrations/002_add.sql", "Database migrations", 25),
    ("app/orders/migrations/0007_x.py", "Database migrations", 25),
    ("infra/main.tf", "Infrastructure and deploy pipeline", 20),
    ("Dockerfile", "Infrastructure and deploy pipeline", 20),
    (".github/workflows/deploy.yml", "Infrastructure and deploy pipeline", 20),
    ("package-lock.json", "Dependencies", 10),
    ("web/requirements-dev.txt", "Dependencies", 10),
    (".env.production", "Configuration", 15),
    ("config/app.yml", "Configuration", 15),
])
def test_builtin_path_categories(path, factor, points):
    assert factors(score([path]))[factor] == points


def test_ordinary_source_file_with_tests_scores_zero():
    result = score(["src/app.py", "tests/test_app.py"])
    assert (result.score, result.level, result.reasons) == (0, "low", [])


def test_category_scores_once_and_lists_a_sample():
    result = score([f"db/migrations/00{i}.sql" for i in range(5)])
    (reason,) = result.reasons
    assert reason.points == 25
    assert reason.detail.startswith("5 files: `db/migrations/000.sql`")
    assert "and 2 more" in reason.detail


def test_custom_rule_takes_precedence_over_builtin():
    cfg = {"paths": [{"name": "Billing", "patterns": ["billing/*"], "points": 30, "reason": "money"}]}
    result = score(["billing/config/rates.yml", "tests/test_x.py"], cfg)
    assert factors(result) == {"Billing": 30}
    assert "(money)" in result.reasons[0].detail


def test_pattern_matching_rules():
    assert matches("a/b/Dockerfile.prod", "Dockerfile*")  # no slash: file name only
    assert not matches("Dockerfile/readme.md", "Dockerfile*")
    assert matches("services/pay/deep/x.py", "services/pay/*")  # * crosses directories
    assert not matches("services/payroll.py", "services/pay/*")


# --- size, blast radius, tests ----------------------------------------------

@pytest.mark.parametrize("lines, points", [(49, None), (50, 6), (200, 12), (500, 18), (1000, 25), (99999, 25)])
def test_size_steps(lines, points):
    result = score([fc("src/a.py", lines, 0), fc("tests/test_a.py", 0, 0)])
    assert factors(result).get("Size") == points


def test_many_files_adds_points():
    result = score([fc(f"docs/page{i}.md", 0, 0) for i in range(30)])
    assert factors(result) == {"Size": 10}


def test_blast_radius_counts_areas_and_service_roots():
    paths = ["services/a/x.md", "services/b/x.md", "services/c/x.md", "README.md"]
    assert factors(score(paths)) == {}  # one top-level area
    assert factors(score(["src/a.md", "tests/b.md", "docs/c.md"])) == {}  # tests and docs are not areas
    result = score(paths, {"services": {"roots": ["services"]}})
    assert factors(result) == {"Blast radius": 10}
    assert "`services/a`, `services/b`, `services/c`" in result.reasons[0].detail
    assert area_of("services/README.md", ("services",)) == "services"
    assert area_of("setup.py", ()) == "(repo root)"


def test_code_without_tests_is_flagged():
    assert factors(score(["src/app.py"])) == {"No test changes": 5}
    assert factors(score(["docs/guide.md"])) == {}


# --- timing -----------------------------------------------------------------

TIMING = {
    "timezone": "America/New_York",
    "business_hours": {"start": "09:00", "end": "17:00", "days": ["mon", "tue", "wed", "thu", "fri"]},
    "freeze_windows": [
        {"name": "Holiday freeze", "start": "2026-12-20T00:00", "end": "2027-01-02T09:00", "points": 40},
        {"name": "Friday afternoon", "days": ["fri"], "start_time": "15:00", "end_time": "23:59", "points": 15},
    ],
}


@pytest.mark.parametrize("now, expected", [
    (datetime(2026, 11, 10, 12, 0), {}),                                      # Tuesday noon
    (datetime(2026, 11, 10, 22, 0), {"Timing": 10}),                          # Tuesday night
    (datetime(2026, 11, 14, 12, 0), {"Timing": 10}),                          # Saturday
    (datetime(2026, 11, 13, 15, 30), {"Freeze window": 15}),                  # Friday afternoon
    (datetime(2026, 12, 22, 11, 0), {"Freeze window": 40}),                   # dated freeze
    (datetime(2026, 11, 10, 17, 0), {"Timing": 10}),                          # end is exclusive
])
def test_timing_rules(now, expected):
    assert factors(score(["docs/a.md"], TIMING, now)) == expected


def test_aware_time_is_converted_to_config_timezone():
    # 02:00 UTC on Wednesday is 21:00 Tuesday in New York: outside business hours.
    result = score(["docs/a.md"], TIMING, datetime(2026, 11, 11, 2, 0, tzinfo=timezone.utc))
    assert "Tue 21:00 EST" in result.reasons[0].detail


def test_no_timing_config_adds_a_note_not_points():
    result = score(["docs/a.md"], None, datetime(2026, 11, 14, 3, 0))
    assert result.reasons == [] and not result.timing_checked
    assert any("Timing was not scored" in n for n in result.notes)


def test_empty_diff_scores_zero_even_inside_a_freeze():
    result = score([], TIMING, datetime(2026, 12, 22, 11, 0))
    assert result.score == 0
    assert "No files changed" in result.notes[0]


# --- score and levels -------------------------------------------------------

def test_score_is_capped_and_reasons_sorted():
    result = score(["db/migrations/1.sql", "infra/main.tf", "a/config/x.yml", "package.json",
                    fc("b/src/app.py", 2000, 0)], TIMING, datetime(2026, 12, 22, 23, 0))
    assert result.score == 100 and result.level == "critical"
    assert [r.points for r in result.reasons] == sorted((r.points for r in result.reasons), reverse=True)
    assert any("capped at 100" in n for n in result.notes)


def test_levels_and_custom_thresholds():
    defaults = {"medium": 25, "high": 50, "critical": 75}
    assert [level_for(s, defaults) for s in (0, 24, 25, 50, 75, 100)] == \
        ["low", "low", "medium", "high", "critical", "critical"]
    assert score(["db/migrations/1.sql"], {"thresholds": {"medium": 30}}).level == "low"


# --- config -----------------------------------------------------------------

def test_example_config_loads():
    cfg = load_config(str(EXAMPLE_CONFIG))
    assert str(cfg.timezone) == "America/New_York"
    assert len(cfg.freeze_windows) == 2 and cfg.business_hours.points == 10
    assert cfg.service_roots == ("services",)


def test_json_config_and_unquoted_yaml_times(tmp_path):
    j = tmp_path / "c.json"
    j.write_text(json.dumps(TIMING))
    assert len(load_config(str(j)).freeze_windows) == 2
    y = tmp_path / "c.yml"
    y.write_text("business_hours:\n  start: 9:30\n  end: 17:00\n"
                 "freeze_windows:\n  - name: f\n    start: 2026-12-20\n    end: 2026-12-27 09:00:00\n")
    cfg = load_config(str(y))
    assert (cfg.business_hours.start.hour, cfg.business_hours.start.minute) == (9, 30)
    assert cfg.freeze_windows[0].start.tzinfo is not None


@pytest.mark.parametrize("data, fragment", [
    ({"timezone": "Mars/Olympus"}, "unknown zone"),
    ({"freeze": []}, "unknown config key"),
    ({"freeze_windows": [{"name": "x"}]}, "needs either start and end, or days"),
    ({"freeze_windows": [{"start": "2026-02-01", "end": "2026-01-01"}]}, "end is before start"),
    ({"freeze_windows": [{"days": ["someday"]}]}, "unknown day"),
    ({"business_hours": {"start": "nine"}}, "expected a time"),
    ({"paths": [{"name": "x"}]}, "needs name and patterns"),
    ({"thresholds": {"medium": 80}}, "medium <= high <= critical"),
    ([1, 2], "expected a mapping"),
])
def test_invalid_config_is_rejected(data, fragment):
    with pytest.raises(ConfigError, match=fragment):
        parse_config(data)


def test_missing_and_malformed_config_files(tmp_path):
    with pytest.raises(ConfigError, match="cannot read config"):
        load_config(str(tmp_path / "nope.yml"))
    bad = tmp_path / "bad.yml"
    bad.write_text("a: [1, 2\n")
    with pytest.raises(ConfigError, match="cannot parse config"):
        load_config(str(bad))


# --- rendering and CLI --------------------------------------------------------

def test_renderers():
    result = score(["db/migrations/1.sql", "src/a|b.py"])
    md = render_markdown(result, "main", "feature")
    assert md.startswith("## Change risk: MEDIUM (35/100)\n")
    assert "| +25 | Database migrations |" in md and "| +5 | Blast radius |" in md
    assert "`main...feature`: 2 files changed, +2 / -0 lines." in md
    assert "+25  Database migrations: 1 file: db/migrations/1.sql" in render_text(result, "main", "feature")
    doc = json.loads(render_json(result, "main", "feature"))
    assert doc["score"] == 35 and doc["level"] == "medium" and len(doc["reasons"]) == 3
    assert "No risk factors found." in render_markdown(score(["docs/a.md"]), "a", "b")


def test_cli_end_to_end(repo, tmp_path, capsys):
    base = ["--repo", str(repo), "--base", "main", "--head", "feature", "--now", "2026-11-10T12:00"]
    out_file = tmp_path / "report.md"
    assert cli.main([*base, "--output", str(out_file)]) == 0
    stdout = capsys.readouterr().out
    assert stdout.startswith("## Change risk: MEDIUM (30/100)")
    assert out_file.read_text() == stdout
    assert cli.main([*base, "--fail-on", "medium"]) == 1
    assert cli.main([*base, "--fail-on", "high"]) == 0


def test_cli_picks_up_repo_config_and_reports_errors(repo, capsys):
    (repo / ".change-risk.yml").write_text(
        "freeze_windows:\n  - name: Always\n    days: [mon, tue, wed, thu, fri, sat, sun]\n    points: 50\n")
    args = ["--repo", str(repo), "--base", "main", "--head", "feature"]
    assert cli.main([*args, "-f", "json"]) == 0
    assert json.loads(capsys.readouterr().out)["score"] == 80
    assert cli.main([*args, "--now", "yesterday"]) == 2
    assert cli.main(["--repo", str(repo), "--base", "missing", "--head", "feature"]) == 2
    assert cli.main([*args, "--config", "does-not-exist.yml"]) == 2
    assert "cannot read config" in capsys.readouterr().err
