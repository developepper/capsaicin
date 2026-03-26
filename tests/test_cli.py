import json
import subprocess

from click.testing import CliRunner

from capsaicin.cli import cli
from capsaicin.init import init_project


def test_help_exits_zero_and_prints_usage():
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "Usage" in result.output


# ---------------------------------------------------------------------------
# Workspace CLI subcommands
# ---------------------------------------------------------------------------


def _bootstrap_project(tmp_path):
    """Create a git repo and init a capsaicin project, return repo path."""
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
    (repo / "readme.txt").write_text("init\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    # Claude permissions
    claude_dir = repo / ".claude"
    claude_dir.mkdir()
    settings = {"permissions": {"allow": ["Edit", "Write"]}}
    (claude_dir / "settings.local.json").write_text(json.dumps(settings))

    init_project("test", str(repo))
    return repo


class TestWorkspaceCli:
    def test_workspace_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["workspace", "--help"])
        assert result.exit_code == 0
        assert "status" in result.output
        assert "recover" in result.output
        assert "cleanup" in result.output

    def test_workspace_status_shared_mode(self, tmp_path):
        repo = _bootstrap_project(tmp_path)
        runner = CliRunner()

        # Add a ticket first.
        add_result = runner.invoke(
            cli,
            [
                "ticket",
                "add",
                "--title",
                "Test ticket",
                "--description",
                "desc",
                "--repo",
                str(repo),
            ],
        )
        assert add_result.exit_code == 0
        # Extract ticket ID from output.
        ticket_id = None
        for line in add_result.output.splitlines():
            if line.startswith("Ticket "):
                ticket_id = line.split()[1]
                break
        assert ticket_id is not None

        result = runner.invoke(
            cli, ["workspace", "status", ticket_id, "--repo", str(repo)]
        )
        assert result.exit_code == 0
        assert "shared" in result.output.lower()

    def test_workspace_status_ticket_not_found(self, tmp_path):
        repo = _bootstrap_project(tmp_path)
        runner = CliRunner()

        result = runner.invoke(
            cli, ["workspace", "status", "nonexistent", "--repo", str(repo)]
        )
        assert result.exit_code != 0
        assert "not found" in result.output.lower()

    def test_workspace_recover_disabled(self, tmp_path):
        repo = _bootstrap_project(tmp_path)
        runner = CliRunner()

        add_result = runner.invoke(
            cli,
            [
                "ticket",
                "add",
                "--title",
                "Test ticket",
                "--description",
                "desc",
                "--repo",
                str(repo),
            ],
        )
        ticket_id = None
        for line in add_result.output.splitlines():
            if line.startswith("Ticket "):
                ticket_id = line.split()[1]
                break

        result = runner.invoke(
            cli, ["workspace", "recover", ticket_id, "--repo", str(repo)]
        )
        assert result.exit_code != 0
        assert "not enabled" in result.output.lower()

    def test_workspace_cleanup_disabled(self, tmp_path):
        repo = _bootstrap_project(tmp_path)
        runner = CliRunner()

        add_result = runner.invoke(
            cli,
            [
                "ticket",
                "add",
                "--title",
                "Test ticket",
                "--description",
                "desc",
                "--repo",
                str(repo),
            ],
        )
        ticket_id = None
        for line in add_result.output.splitlines():
            if line.startswith("Ticket "):
                ticket_id = line.split()[1]
                break

        result = runner.invoke(
            cli, ["workspace", "cleanup", ticket_id, "--repo", str(repo)]
        )
        assert result.exit_code != 0
        assert "not enabled" in result.output.lower()


class TestDoctorWorkspace:
    def test_doctor_includes_workspace_check(self, tmp_path):
        repo = _bootstrap_project(tmp_path)
        runner = CliRunner()

        result = runner.invoke(cli, ["doctor", "--repo", str(repo)])
        # Should include workspace readiness check output.
        assert "workspace" in result.output.lower() or "Workspace" in result.output
