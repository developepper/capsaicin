from click.testing import CliRunner

from capsaicin.cli import cli


def test_help_exits_zero_and_prints_usage():
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "Usage" in result.output
