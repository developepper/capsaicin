"""Tests for preflight check module (T04).

Covers:
- missing command on PATH
- invalid repo path
- non-git repo directory
- dirty working tree (warning)
- clean working tree
- missing Claude settings file
- invalid JSON in Claude settings
- missing permissions key
- missing Edit or Write permissions
- valid permissions with Edit and Write
- Bash patterns not validated
- structured check results
- aggregated preflight report
"""

from __future__ import annotations

import json
import subprocess

import pytest

from capsaicin.preflight import (
    CheckResult,
    PreflightReport,
    check_claude_permissions,
    check_command_on_path,
    check_is_git_repo,
    check_repo_path_exists,
    check_working_tree_clean,
    run_preflight,
)


# ---------------------------------------------------------------------------
# CheckResult basics
# ---------------------------------------------------------------------------


class TestCheckResult:
    def test_valid_statuses(self):
        for status in ("pass", "warn", "fail"):
            cr = CheckResult(name="test", status=status, message="ok")
            assert cr.status == status

    def test_invalid_status_raises(self):
        with pytest.raises(ValueError, match="Invalid check status"):
            CheckResult(name="test", status="error", message="bad")


# ---------------------------------------------------------------------------
# check_command_on_path
# ---------------------------------------------------------------------------


class TestCheckCommandOnPath:
    def test_existing_command(self):
        result = check_command_on_path("git")
        assert result.status == "pass"
        assert result.name == "command_available"

    def test_missing_command(self):
        result = check_command_on_path("nonexistent_binary_xyz_123")
        assert result.status == "fail"
        assert "not found" in result.message


# ---------------------------------------------------------------------------
# check_repo_path_exists
# ---------------------------------------------------------------------------


class TestCheckRepoPathExists:
    def test_existing_path(self, tmp_path):
        result = check_repo_path_exists(tmp_path)
        assert result.status == "pass"

    def test_missing_path(self, tmp_path):
        result = check_repo_path_exists(tmp_path / "nonexistent")
        assert result.status == "fail"
        assert "does not exist" in result.message


# ---------------------------------------------------------------------------
# check_is_git_repo
# ---------------------------------------------------------------------------


class TestCheckIsGitRepo:
    def test_git_repo(self, tmp_path):
        subprocess.run(
            ["git", "init"], cwd=tmp_path, check=True, capture_output=True
        )
        result = check_is_git_repo(tmp_path)
        assert result.status == "pass"

    def test_non_git_directory(self, tmp_path):
        result = check_is_git_repo(tmp_path)
        assert result.status == "fail"
        assert "Not a git repository" in result.message

    def test_nonexistent_directory(self, tmp_path):
        result = check_is_git_repo(tmp_path / "missing")
        assert result.status == "fail"


# ---------------------------------------------------------------------------
# check_working_tree_clean
# ---------------------------------------------------------------------------


class TestCheckWorkingTreeClean:
    def test_clean_tree(self, tmp_path):
        subprocess.run(
            ["git", "init"], cwd=tmp_path, check=True, capture_output=True
        )
        subprocess.run(
            ["git", "config", "user.email", "t@t.com"],
            cwd=tmp_path, check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "T"],
            cwd=tmp_path, check=True, capture_output=True,
        )
        (tmp_path / "f.txt").write_text("x")
        subprocess.run(
            ["git", "add", "."], cwd=tmp_path, check=True, capture_output=True
        )
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=tmp_path, check=True, capture_output=True,
        )
        result = check_working_tree_clean(tmp_path)
        assert result.status == "pass"

    def test_dirty_tree_is_warning(self, tmp_path):
        subprocess.run(
            ["git", "init"], cwd=tmp_path, check=True, capture_output=True
        )
        subprocess.run(
            ["git", "config", "user.email", "t@t.com"],
            cwd=tmp_path, check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "T"],
            cwd=tmp_path, check=True, capture_output=True,
        )
        (tmp_path / "f.txt").write_text("x")
        subprocess.run(
            ["git", "add", "."], cwd=tmp_path, check=True, capture_output=True
        )
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=tmp_path, check=True, capture_output=True,
        )
        # Make it dirty
        (tmp_path / "f.txt").write_text("changed")
        result = check_working_tree_clean(tmp_path)
        assert result.status == "warn"
        assert "uncommitted" in result.message.lower()


# ---------------------------------------------------------------------------
# check_claude_permissions
# ---------------------------------------------------------------------------


class TestCheckClaudePermissions:
    def test_missing_settings_file(self, tmp_path):
        result = check_claude_permissions(tmp_path)
        assert result.status == "fail"
        assert "not found" in result.message

    def test_invalid_json(self, tmp_path):
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "settings.local.json").write_text("not json{")
        result = check_claude_permissions(tmp_path)
        assert result.status == "fail"
        assert "parse" in result.message.lower()

    def test_not_json_object(self, tmp_path):
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "settings.local.json").write_text('"just a string"')
        result = check_claude_permissions(tmp_path)
        assert result.status == "fail"
        assert "not a JSON object" in result.message

    def test_missing_permissions_key(self, tmp_path):
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "settings.local.json").write_text(json.dumps({"other": 1}))
        result = check_claude_permissions(tmp_path)
        assert result.status == "fail"
        assert "permissions" in result.message.lower()

    def test_missing_allow_array(self, tmp_path):
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "settings.local.json").write_text(
            json.dumps({"permissions": {"deny": []}})
        )
        result = check_claude_permissions(tmp_path)
        assert result.status == "fail"
        assert "allow" in result.message.lower()

    def test_missing_edit(self, tmp_path):
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        settings = {"permissions": {"allow": ["Write", "Bash(git:*)"]}}
        (claude_dir / "settings.local.json").write_text(json.dumps(settings))
        result = check_claude_permissions(tmp_path)
        assert result.status == "fail"
        assert "Edit" in result.message

    def test_missing_write(self, tmp_path):
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        settings = {"permissions": {"allow": ["Edit", "Bash(git:*)"]}}
        (claude_dir / "settings.local.json").write_text(json.dumps(settings))
        result = check_claude_permissions(tmp_path)
        assert result.status == "fail"
        assert "Write" in result.message

    def test_missing_both_edit_and_write(self, tmp_path):
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        settings = {"permissions": {"allow": ["Bash(git:*)"]}}
        (claude_dir / "settings.local.json").write_text(json.dumps(settings))
        result = check_claude_permissions(tmp_path)
        assert result.status == "fail"
        assert "Edit" in result.message
        assert "Write" in result.message

    def test_valid_permissions(self, tmp_path):
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        settings = {
            "permissions": {
                "allow": [
                    "Bash(wc:*)",
                    "Edit",
                    "Write",
                    "Read(//opt/homebrew/bin/**)",
                ]
            }
        }
        (claude_dir / "settings.local.json").write_text(json.dumps(settings))
        result = check_claude_permissions(tmp_path)
        assert result.status == "pass"
        assert "Edit" in result.message
        assert "Write" in result.message

    def test_non_string_entries_do_not_crash(self, tmp_path):
        """Non-string entries in allow array should not raise TypeError."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        settings = {"permissions": {"allow": ["Edit", 42, {"x": 1}]}}
        (claude_dir / "settings.local.json").write_text(json.dumps(settings))
        result = check_claude_permissions(tmp_path)
        # Write is missing, so this should fail — but must not crash
        assert result.status == "fail"
        assert "Write" in result.message

    def test_non_string_entries_with_both_tools_present(self, tmp_path):
        """Non-string junk alongside valid Edit/Write should still pass."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        settings = {"permissions": {"allow": ["Edit", "Write", 42, {"x": 1}]}}
        (claude_dir / "settings.local.json").write_text(json.dumps(settings))
        result = check_claude_permissions(tmp_path)
        assert result.status == "pass"

    def test_bash_patterns_not_validated(self, tmp_path):
        """Bash allow-list patterns should not cause failure."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        settings = {
            "permissions": {
                "allow": ["Edit", "Write"]
                # No Bash entries — should still pass
            }
        }
        (claude_dir / "settings.local.json").write_text(json.dumps(settings))
        result = check_claude_permissions(tmp_path)
        assert result.status == "pass"


# ---------------------------------------------------------------------------
# PreflightReport
# ---------------------------------------------------------------------------


class TestPreflightReport:
    def test_empty_report_passes(self):
        r = PreflightReport()
        assert r.passed
        assert not r.has_warnings

    def test_with_failure(self):
        r = PreflightReport(checks=[
            CheckResult(name="a", status="pass", message="ok"),
            CheckResult(name="b", status="fail", message="bad"),
        ])
        assert not r.passed
        assert len(r.failures) == 1

    def test_with_warning(self):
        r = PreflightReport(checks=[
            CheckResult(name="a", status="pass", message="ok"),
            CheckResult(name="b", status="warn", message="hmm"),
        ])
        assert r.passed
        assert r.has_warnings
        assert len(r.warnings) == 1


# ---------------------------------------------------------------------------
# run_preflight integration
# ---------------------------------------------------------------------------


class TestRunPreflight:
    def test_valid_environment(self, tmp_path):
        """Full preflight on a valid git repo with Claude permissions."""
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(
            ["git", "init"], cwd=repo, check=True, capture_output=True
        )
        subprocess.run(
            ["git", "config", "user.email", "t@t.com"],
            cwd=repo, check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "T"],
            cwd=repo, check=True, capture_output=True,
        )
        (repo / "f.txt").write_text("x")
        subprocess.run(
            ["git", "add", "."], cwd=repo, check=True, capture_output=True
        )
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=repo, check=True, capture_output=True,
        )
        claude_dir = repo / ".claude"
        claude_dir.mkdir()
        settings = {"permissions": {"allow": ["Edit", "Write"]}}
        (claude_dir / "settings.local.json").write_text(json.dumps(settings))

        report = run_preflight(repo, adapter_command="git")
        assert report.passed
        check_names = [c.name for c in report.checks]
        assert "command_available" in check_names
        assert "repo_path_exists" in check_names
        assert "is_git_repo" in check_names
        assert "working_tree_clean" in check_names
        assert "claude_permissions" in check_names

    def test_missing_command_fails(self, tmp_path):
        report = run_preflight(tmp_path, adapter_command="nonexistent_xyz")
        assert not report.passed
        failed_names = [c.name for c in report.failures]
        assert "command_available" in failed_names
