"""Tests for config loading (T04)."""

from __future__ import annotations

import pytest

from capsaicin.config import (
    AdapterConfig,
    Config,
    ConfigError,
    config_to_snapshot,
    load_config,
    resolve_project,
    write_default_config,
)

MINIMAL_TOML = """\
[project]
name = "test-proj"
repo_path = "/tmp/repo"

[adapters.implementer]
backend = "claude-code"
command = "claude"

[adapters.reviewer]
backend = "claude-code"
command = "claude"

[limits]
"""

FULL_TOML = """\
[project]
name = "my-project"
repo_path = "/home/user/repo"

[adapters.implementer]
backend = "claude-code"
command = "claude"
model = "opus"

[adapters.reviewer]
backend = "claude-code"
command = "claude"
allowed_tools = ["Read", "Glob", "Grep", "Bash"]

[limits]
max_cycles = 5
max_impl_retries = 3
max_review_retries = 4
timeout_seconds = 600

[reviewer]
mode = "read-only"

[ticket_selection]
order = "priority"

[paths]
renders_dir = "output/renders"
exports_dir = "output/exports"
"""

FOUR_ROLE_TOML = """\
[project]
name = "four-role"
repo_path = "/tmp/repo"

[adapters.implementer]
backend = "claude-code"
command = "claude-impl"

[adapters.reviewer]
backend = "claude-code"
command = "claude-rev"
allowed_tools = ["Read"]

[adapters.planner]
backend = "claude-code"
command = "claude-plan"
model = "sonnet"

[adapters.planning_reviewer]
backend = "claude-code"
command = "claude-planrev"
allowed_tools = ["Read", "Grep"]

[limits]
"""


class TestLoadConfig:
    def test_parses_minimal_config(self, tmp_path):
        cfg_path = tmp_path / "config.toml"
        cfg_path.write_text(MINIMAL_TOML)
        cfg = load_config(cfg_path)
        assert isinstance(cfg, Config)
        assert cfg.project.name == "test-proj"
        assert cfg.project.repo_path == "/tmp/repo"

    def test_parses_full_config(self, tmp_path):
        cfg_path = tmp_path / "config.toml"
        cfg_path.write_text(FULL_TOML)
        cfg = load_config(cfg_path)
        assert cfg.project.name == "my-project"
        assert cfg.implementer.model == "opus"
        assert cfg.reviewer.allowed_tools == ["Read", "Glob", "Grep", "Bash"]
        assert cfg.limits.max_cycles == 5
        assert cfg.limits.timeout_seconds == 600
        assert cfg.ticket_selection.order == "priority"
        assert cfg.paths.renders_dir == "output/renders"

    def test_applies_defaults_for_limits(self, tmp_path):
        cfg_path = tmp_path / "config.toml"
        cfg_path.write_text(MINIMAL_TOML)
        cfg = load_config(cfg_path)
        assert cfg.limits.max_cycles == 3
        assert cfg.limits.max_impl_retries == 2
        assert cfg.limits.max_review_retries == 2
        assert cfg.limits.timeout_seconds == 300

    def test_applies_defaults_for_optional_sections(self, tmp_path):
        cfg_path = tmp_path / "config.toml"
        cfg_path.write_text(MINIMAL_TOML)
        cfg = load_config(cfg_path)
        assert cfg.reviewer_policy.mode == "read-only"
        assert cfg.ticket_selection.order == "created_at"
        assert cfg.paths.renders_dir == "renders"
        assert cfg.paths.exports_dir == "exports"

    def test_adapter_model_defaults_to_none(self, tmp_path):
        cfg_path = tmp_path / "config.toml"
        cfg_path.write_text(MINIMAL_TOML)
        cfg = load_config(cfg_path)
        assert cfg.implementer.model is None

    def test_adapter_allowed_tools_defaults_to_empty(self, tmp_path):
        cfg_path = tmp_path / "config.toml"
        cfg_path.write_text(MINIMAL_TOML)
        cfg = load_config(cfg_path)
        assert cfg.implementer.allowed_tools == []

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(ConfigError, match="Config file not found"):
            load_config(tmp_path / "nonexistent.toml")

    def test_missing_project_section_raises(self, tmp_path):
        cfg_path = tmp_path / "config.toml"
        cfg_path.write_text(
            "[limits]\n[adapters.implementer]\nbackend='x'\ncommand='x'\n[adapters.reviewer]\nbackend='x'\ncommand='x'\n"
        )
        with pytest.raises(ConfigError, match=r"\[project\]"):
            load_config(cfg_path)

    def test_missing_limits_section_raises(self, tmp_path):
        cfg_path = tmp_path / "config.toml"
        cfg_path.write_text(
            "[project]\nname='x'\nrepo_path='/x'\n[adapters.implementer]\nbackend='x'\ncommand='x'\n[adapters.reviewer]\nbackend='x'\ncommand='x'\n"
        )
        with pytest.raises(ConfigError, match=r"\[limits\]"):
            load_config(cfg_path)

    def test_missing_implementer_adapter_raises(self, tmp_path):
        cfg_path = tmp_path / "config.toml"
        cfg_path.write_text(
            "[project]\nname='x'\nrepo_path='/x'\n[limits]\n[adapters.reviewer]\nbackend='x'\ncommand='x'\n"
        )
        with pytest.raises(ConfigError, match=r"\[adapters\.implementer\]"):
            load_config(cfg_path)

    def test_missing_reviewer_adapter_raises(self, tmp_path):
        cfg_path = tmp_path / "config.toml"
        cfg_path.write_text(
            "[project]\nname='x'\nrepo_path='/x'\n[limits]\n[adapters.implementer]\nbackend='x'\ncommand='x'\n"
        )
        with pytest.raises(ConfigError, match=r"\[adapters\.reviewer\]"):
            load_config(cfg_path)

    def test_missing_project_name_raises(self, tmp_path):
        cfg_path = tmp_path / "config.toml"
        cfg_path.write_text(
            "[project]\nrepo_path='/x'\n[limits]\n[adapters.implementer]\nbackend='x'\ncommand='x'\n[adapters.reviewer]\nbackend='x'\ncommand='x'\n"
        )
        with pytest.raises(ConfigError, match="name"):
            load_config(cfg_path)

    def test_missing_adapter_backend_raises(self, tmp_path):
        cfg_path = tmp_path / "config.toml"
        cfg_path.write_text(
            "[project]\nname='x'\nrepo_path='/x'\n[limits]\n[adapters.implementer]\ncommand='x'\n[adapters.reviewer]\nbackend='x'\ncommand='x'\n"
        )
        with pytest.raises(ConfigError, match="backend"):
            load_config(cfg_path)


class TestResolveProject:
    def test_single_project_resolved(self, tmp_path):
        root = tmp_path / ".capsaicin"
        (root / "projects" / "my-proj").mkdir(parents=True)
        slug = resolve_project(root)
        assert slug == "my-proj"

    def test_no_projects_dir_raises(self, tmp_path):
        with pytest.raises(ConfigError, match="No projects directory"):
            resolve_project(tmp_path / "nonexistent")

    def test_empty_projects_dir_raises(self, tmp_path):
        root = tmp_path / ".capsaicin"
        (root / "projects").mkdir(parents=True)
        with pytest.raises(ConfigError, match="No projects found"):
            resolve_project(root)

    def test_multiple_projects_raises(self, tmp_path):
        root = tmp_path / ".capsaicin"
        (root / "projects" / "proj-a").mkdir(parents=True)
        (root / "projects" / "proj-b").mkdir(parents=True)
        with pytest.raises(ConfigError, match="Multiple projects"):
            resolve_project(root)

    def test_files_ignored_only_dirs_counted(self, tmp_path):
        root = tmp_path / ".capsaicin"
        (root / "projects" / "my-proj").mkdir(parents=True)
        (root / "projects" / "stray-file.txt").write_text("ignore me")
        slug = resolve_project(root)
        assert slug == "my-proj"


class TestWriteDefaultConfig:
    def test_writes_parseable_config(self, tmp_path):
        cfg_path = tmp_path / "config.toml"
        write_default_config(cfg_path, "test-proj", "/abs/repo")
        cfg = load_config(cfg_path)
        assert cfg.project.name == "test-proj"
        assert cfg.project.repo_path == "/abs/repo"
        assert cfg.implementer.backend == "claude-code"
        assert cfg.reviewer.allowed_tools == ["Read", "Glob", "Grep", "Bash"]
        assert cfg.limits.max_cycles == 3

    def test_file_created(self, tmp_path):
        cfg_path = tmp_path / "config.toml"
        write_default_config(cfg_path, "p", "/r")
        assert cfg_path.exists()

    def test_special_characters_in_name_and_path(self, tmp_path):
        cfg_path = tmp_path / "config.toml"
        write_default_config(cfg_path, 'proj "quoted"', '/tmp/path with "quotes"')
        cfg = load_config(cfg_path)
        assert cfg.project.name == 'proj "quoted"'
        assert cfg.project.repo_path == '/tmp/path with "quotes"'

    def test_backslash_in_path(self, tmp_path):
        cfg_path = tmp_path / "config.toml"
        write_default_config(cfg_path, "proj", "C:\\Users\\dev\\repo")
        cfg = load_config(cfg_path)
        assert cfg.project.repo_path == "C:\\Users\\dev\\repo"

    def test_newline_in_name(self, tmp_path):
        cfg_path = tmp_path / "config.toml"
        write_default_config(cfg_path, "line1\nline2", "/repo")
        cfg = load_config(cfg_path)
        assert cfg.project.name == "line1\nline2"


class TestFourRoleConfig:
    def test_planner_and_planning_reviewer_parsed(self, tmp_path):
        cfg_path = tmp_path / "config.toml"
        cfg_path.write_text(FOUR_ROLE_TOML)
        cfg = load_config(cfg_path)
        assert cfg.planner is not None
        assert cfg.planner.command == "claude-plan"
        assert cfg.planner.model == "sonnet"
        assert cfg.planning_reviewer is not None
        assert cfg.planning_reviewer.command == "claude-planrev"
        assert cfg.planning_reviewer.allowed_tools == ["Read", "Grep"]

    def test_resolved_planner_returns_explicit_when_set(self, tmp_path):
        cfg_path = tmp_path / "config.toml"
        cfg_path.write_text(FOUR_ROLE_TOML)
        cfg = load_config(cfg_path)
        assert cfg.resolved_planner.command == "claude-plan"

    def test_resolved_planning_reviewer_returns_explicit_when_set(self, tmp_path):
        cfg_path = tmp_path / "config.toml"
        cfg_path.write_text(FOUR_ROLE_TOML)
        cfg = load_config(cfg_path)
        assert cfg.resolved_planning_reviewer.command == "claude-planrev"

    def test_planner_defaults_to_implementer_when_absent(self, tmp_path):
        cfg_path = tmp_path / "config.toml"
        cfg_path.write_text(MINIMAL_TOML)
        cfg = load_config(cfg_path)
        assert cfg.planner is None
        assert cfg.resolved_planner is cfg.implementer

    def test_planning_reviewer_defaults_to_reviewer_when_absent(self, tmp_path):
        cfg_path = tmp_path / "config.toml"
        cfg_path.write_text(MINIMAL_TOML)
        cfg = load_config(cfg_path)
        assert cfg.planning_reviewer is None
        assert cfg.resolved_planning_reviewer is cfg.reviewer

    def test_existing_configs_work_unchanged(self, tmp_path):
        """Configs without planner/planning_reviewer sections still load."""
        cfg_path = tmp_path / "config.toml"
        cfg_path.write_text(FULL_TOML)
        cfg = load_config(cfg_path)
        assert cfg.planner is None
        assert cfg.planning_reviewer is None
        assert cfg.resolved_planner.command == "claude"
        assert cfg.resolved_planning_reviewer.command == "claude"


class TestConfigToSnapshotFourRoles:
    def test_snapshot_includes_all_four_roles_explicit(self, tmp_path):
        cfg_path = tmp_path / "config.toml"
        cfg_path.write_text(FOUR_ROLE_TOML)
        cfg = load_config(cfg_path)
        snap = config_to_snapshot(cfg)
        assert snap["adapters"]["implementer"]["command"] == "claude-impl"
        assert snap["adapters"]["reviewer"]["command"] == "claude-rev"
        assert snap["adapters"]["planner"]["command"] == "claude-plan"
        assert snap["adapters"]["planner"]["model"] == "sonnet"
        assert snap["adapters"]["planning_reviewer"]["command"] == "claude-planrev"
        assert snap["adapters"]["planning_reviewer"]["allowed_tools"] == [
            "Read",
            "Grep",
        ]

    def test_snapshot_fallback_planner_matches_implementer(self, tmp_path):
        cfg_path = tmp_path / "config.toml"
        cfg_path.write_text(MINIMAL_TOML)
        cfg = load_config(cfg_path)
        snap = config_to_snapshot(cfg)
        assert snap["adapters"]["planner"] == snap["adapters"]["implementer"]
        assert snap["adapters"]["planning_reviewer"] == snap["adapters"]["reviewer"]

    def test_snapshot_round_trips_all_four(self, tmp_path):
        cfg_path = tmp_path / "config.toml"
        cfg_path.write_text(FOUR_ROLE_TOML)
        cfg = load_config(cfg_path)
        snap = config_to_snapshot(cfg)
        assert set(snap["adapters"].keys()) == {
            "implementer",
            "reviewer",
            "planner",
            "planning_reviewer",
        }
        for role in ("implementer", "reviewer", "planner", "planning_reviewer"):
            adapter_snap = snap["adapters"][role]
            assert "backend" in adapter_snap
            assert "command" in adapter_snap
            assert "model" in adapter_snap
            assert "allowed_tools" in adapter_snap


class TestResolveAdapter:
    def setup_method(self):
        """Reset registry state between tests."""
        from capsaicin.adapters import registry

        registry._REGISTRY.clear()
        registry._DEFAULTS_LOADED = False

    def test_resolves_claude_code(self):
        from capsaicin.adapters.claude_code import ClaudeCodeAdapter
        from capsaicin.adapters.registry import resolve_adapter

        cls = resolve_adapter("claude-code")
        assert cls is ClaudeCodeAdapter

    def test_unknown_backend_raises(self):
        from capsaicin.adapters.registry import resolve_adapter

        with pytest.raises(ValueError, match="Unknown adapter backend"):
            resolve_adapter("nonexistent")

    def test_build_adapter_from_config(self):
        from capsaicin.adapters.claude_code import ClaudeCodeAdapter
        from capsaicin.adapters.registry import build_adapter_from_config

        ac = AdapterConfig(backend="claude-code", command="my-claude")
        adapter = build_adapter_from_config(ac)
        assert isinstance(adapter, ClaudeCodeAdapter)

    def test_register_adapter(self):
        from capsaicin.adapters.base import BaseAdapter
        from capsaicin.adapters.registry import register_adapter, resolve_adapter
        from capsaicin.adapters.types import RunRequest, RunResult

        class FakeAdapter(BaseAdapter):
            def __init__(self, command="fake"):
                self.command = command

            def execute(self, request: RunRequest) -> RunResult:
                return RunResult(run_id=request.run_id, exit_status="success")

        register_adapter("fake", FakeAdapter)
        assert resolve_adapter("fake") is FakeAdapter

    def test_register_adapter_duplicate_raises(self):
        from capsaicin.adapters.base import BaseAdapter
        from capsaicin.adapters.registry import register_adapter
        from capsaicin.adapters.types import RunRequest, RunResult

        class FakeAdapter(BaseAdapter):
            def __init__(self, command="fake"):
                self.command = command

            def execute(self, request: RunRequest) -> RunResult:
                return RunResult(run_id=request.run_id, exit_status="success")

        register_adapter("fake", FakeAdapter)
        with pytest.raises(ValueError, match="Adapter already registered"):
            register_adapter("fake", FakeAdapter)
