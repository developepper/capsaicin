"""Tests for ``capsaicin doctor`` command (T05).

Covers:
- all checks pass with clean environment
- missing command causes non-zero exit
- warnings render without failing
- missing Claude permissions show remediation text
- output is checklist-style with [OK], [WARN], [FAIL]
"""

from __future__ import annotations

import json
import subprocess
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from capsaicin.cli import cli


@pytest.fixture()
def clean_repo(tmp_path):
    """A clean git repo with Claude permissions configured."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "t@t.com"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "T"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    (repo / "f.txt").write_text("x")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    claude_dir = repo / ".claude"
    claude_dir.mkdir()
    settings = {"permissions": {"allow": ["Edit", "Write"]}}
    (claude_dir / "settings.local.json").write_text(json.dumps(settings))
    return repo


class TestDoctorAllPass:
    @patch("capsaicin.preflight.shutil.which", return_value="/usr/bin/claude")
    def test_exits_zero_when_all_pass(self, _mock_which, clean_repo):
        runner = CliRunner()
        result = runner.invoke(cli, ["doctor", "--repo", str(clean_repo)])
        assert result.exit_code == 0
        assert "All checks passed" in result.output

    @patch("capsaicin.preflight.shutil.which", return_value="/usr/bin/claude")
    def test_shows_ok_markers(self, _mock_which, clean_repo):
        runner = CliRunner()
        result = runner.invoke(cli, ["doctor", "--repo", str(clean_repo)])
        assert "[OK]" in result.output


class TestDoctorFailures:
    def test_nonzero_exit_on_failure(self, tmp_path):
        """Non-git directory should fail is_git_repo check."""
        runner = CliRunner()
        result = runner.invoke(cli, ["doctor", "--repo", str(tmp_path)])
        assert result.exit_code != 0
        assert "[FAIL]" in result.output

    def test_missing_permissions_shows_remediation(self, tmp_path):
        """Missing Claude settings should show actionable fix text."""
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        runner = CliRunner()
        result = runner.invoke(cli, ["doctor", "--repo", str(repo)])
        assert result.exit_code != 0
        assert "not found" in result.output.lower() or "FAIL" in result.output

    def test_missing_edit_write_shows_tool_names(self, tmp_path):
        """When Edit/Write are missing, output should name them."""
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        claude_dir = repo / ".claude"
        claude_dir.mkdir()
        settings = {"permissions": {"allow": ["Bash(git:*)"]}}
        (claude_dir / "settings.local.json").write_text(json.dumps(settings))

        runner = CliRunner()
        result = runner.invoke(cli, ["doctor", "--repo", str(repo)])
        assert result.exit_code != 0
        assert "Edit" in result.output
        assert "Write" in result.output

    def test_failure_count_in_summary(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(cli, ["doctor", "--repo", str(tmp_path)])
        assert "check(s) failed" in result.output


class TestDoctorProjectResolution:
    def test_missing_project_slug_errors(self, clean_repo):
        """--project with a nonexistent slug should error, not silently pass."""
        # Init capsaicin so .capsaicin/ exists
        from capsaicin.init import init_project

        init_project("real-proj", str(clean_repo))

        runner = CliRunner()
        result = runner.invoke(
            cli, ["doctor", "--repo", str(clean_repo), "--project", "nonexistent"]
        )
        assert result.exit_code != 0
        assert "not found" in result.output.lower()

    @patch("capsaicin.preflight.shutil.which", return_value="/usr/bin/claude")
    def test_valid_project_slug_works(self, _mock_which, clean_repo):
        from capsaicin.init import init_project

        init_project("my-proj", str(clean_repo))

        runner = CliRunner()
        result = runner.invoke(
            cli, ["doctor", "--repo", str(clean_repo), "--project", "my-proj"]
        )
        assert result.exit_code == 0
        assert "All checks passed" in result.output


class TestDoctorWarnings:
    @patch("capsaicin.preflight.shutil.which", return_value="/usr/bin/claude")
    def test_dirty_tree_warns_but_passes(self, _mock_which, clean_repo):
        """Dirty working tree should warn, not fail."""
        (clean_repo / "f.txt").write_text("dirty")
        runner = CliRunner()
        result = runner.invoke(cli, ["doctor", "--repo", str(clean_repo)])
        assert result.exit_code == 0
        assert "[WARN]" in result.output
        assert "warning(s)" in result.output


class TestDoctorHelp:
    def test_help_text(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["doctor", "--help"])
        assert result.exit_code == 0
        assert (
            "preflight" in result.output.lower() or "validate" in result.output.lower()
        )
