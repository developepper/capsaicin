"""Tests for the planning loop orchestration (T04)."""

from __future__ import annotations

import pytest

from capsaicin.adapters.types import (
    PlannedAcceptanceCriterion,
    PlannedEpicData,
    PlannedTicketData,
    PlannerResult,
    PlanningFinding,
    PlanningReviewResult,
    PlanningScopeReviewed,
    RunResult,
)
from capsaicin.orchestrator import get_state
from capsaicin.planning_loop import run_planning_loop
from capsaicin.planning_review import run_planning_review_pipeline
from capsaicin.planning_run import run_draft_pipeline


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _add_epic(env, problem="Build a REST API"):
    """Create a new planning epic and return its ID."""
    from capsaicin.app.commands.new_epic import new_epic

    result = new_epic(
        conn=env["conn"],
        project_id=env["project_id"],
        problem_statement=problem,
        log_path=env["log_path"],
    )
    return result.epic_id


def _get_epic_status(conn, epic_id):
    row = conn.execute(
        "SELECT status FROM planned_epics WHERE id = ?", (epic_id,)
    ).fetchone()
    return row["status"]


class MockPlanningAdapter:
    """Adapter that returns predetermined results in sequence."""

    def __init__(self, results=None):
        self.calls = []
        self._results = list(results or [])
        self._index = 0

    def execute(self, request):
        self.calls.append(request)
        if self._index < len(self._results):
            result = self._results[self._index]
            self._index += 1
            return result
        # Default: success with no structured result (will trigger parse_error)
        return RunResult(
            run_id="mock",
            exit_status="success",
            duration_seconds=1.0,
            raw_stdout="ok",
            raw_stderr="",
        )


def _make_planner_result():
    """Create a valid PlannerResult."""
    return PlannerResult(
        epic=PlannedEpicData(
            title="REST API Epic",
            summary="Build a REST API with CRUD operations",
            success_outcome="Functioning REST API with tests",
        ),
        tickets=[
            PlannedTicketData(
                sequence=1,
                title="Data model",
                goal="Define data models",
                scope=["Create SQLAlchemy models"],
                non_goals=["Frontend"],
                acceptance_criteria=[
                    PlannedAcceptanceCriterion(description="Models created")
                ],
                dependencies=[],
                references=["src/models.py"],
                implementation_notes=["Use SQLAlchemy"],
            ),
            PlannedTicketData(
                sequence=2,
                title="API endpoints",
                goal="Implement CRUD endpoints",
                scope=["Create REST endpoints"],
                non_goals=["Auth"],
                acceptance_criteria=[
                    PlannedAcceptanceCriterion(description="Endpoints work")
                ],
                dependencies=[1],
                references=["src/api.py"],
                implementation_notes=["Use FastAPI"],
            ),
        ],
        sequencing_notes="Model first, then API",
    )


def _make_draft_success():
    """Create a successful planner run result."""
    return RunResult(
        run_id="mock",
        exit_status="success",
        duration_seconds=1.0,
        raw_stdout="ok",
        raw_stderr="",
        structured_result=_make_planner_result(),
    )


def _make_draft_failure():
    return RunResult(
        run_id="mock",
        exit_status="failure",
        duration_seconds=1.0,
        raw_stdout="",
        raw_stderr="error",
    )


def _make_review_pass():
    return RunResult(
        run_id="mock",
        exit_status="success",
        duration_seconds=1.0,
        raw_stdout="ok",
        raw_stderr="",
        structured_result=PlanningReviewResult(
            verdict="pass",
            confidence="high",
            findings=[],
            scope_reviewed=PlanningScopeReviewed(
                epic_reviewed=True,
                tickets_reviewed=[1, 2],
            ),
        ),
    )


def _make_review_fail():
    return RunResult(
        run_id="mock",
        exit_status="success",
        duration_seconds=1.0,
        raw_stdout="ok",
        raw_stderr="",
        structured_result=PlanningReviewResult(
            verdict="fail",
            confidence="high",
            findings=[
                PlanningFinding(
                    severity="blocking",
                    category="completeness",
                    description="Missing error handling ticket",
                    target_type="epic",
                )
            ],
            scope_reviewed=PlanningScopeReviewed(
                epic_reviewed=True,
                tickets_reviewed=[1, 2],
            ),
        ),
    )


def _make_review_escalate():
    return RunResult(
        run_id="mock",
        exit_status="success",
        duration_seconds=1.0,
        raw_stdout="ok",
        raw_stderr="",
        structured_result=PlanningReviewResult(
            verdict="escalate",
            confidence="low",
            findings=[],
            scope_reviewed=PlanningScopeReviewed(
                epic_reviewed=True,
                tickets_reviewed=[1, 2],
            ),
        ),
    )


# ---------------------------------------------------------------------------
# Clean pass to human gate: draft -> review pass -> human-gate
# ---------------------------------------------------------------------------


class TestPlanningLoopPassToHumanGate:
    def test_stops_at_human_gate_on_pass(self, project_env):
        env = project_env
        eid = _add_epic(env)

        adapter = MockPlanningAdapter(
            results=[_make_draft_success(), _make_review_pass()]
        )
        final_status, detail = run_planning_loop(
            env["conn"],
            env["project_id"],
            env["config"],
            adapter,
            adapter,
            epic_id=eid,
            log_path=env["log_path"],
        )

        assert final_status == "human-gate"
        assert "Awaiting human decision" in detail
        assert "review_passed" in detail
        assert _get_epic_status(env["conn"], eid) == "human-gate"
        assert len(adapter.calls) == 2

    def test_never_auto_approves(self, project_env):
        env = project_env
        eid = _add_epic(env)

        adapter = MockPlanningAdapter(
            results=[_make_draft_success(), _make_review_pass()]
        )
        final_status, _ = run_planning_loop(
            env["conn"],
            env["project_id"],
            env["config"],
            adapter,
            adapter,
            epic_id=eid,
            log_path=env["log_path"],
        )

        assert final_status == "human-gate"
        assert _get_epic_status(env["conn"], eid) != "approved"


# ---------------------------------------------------------------------------
# Fail to revise: draft -> review fail -> draft -> review pass
# ---------------------------------------------------------------------------


class TestPlanningLoopRevise:
    def test_loops_on_fail_then_passes(self, project_env):
        env = project_env
        eid = _add_epic(env)

        # Sequence: draft, review fail, draft (revise), review pass
        adapter = MockPlanningAdapter(
            results=[
                _make_draft_success(),
                _make_review_fail(),
                _make_draft_success(),
                _make_review_pass(),
            ]
        )
        final_status, detail = run_planning_loop(
            env["conn"],
            env["project_id"],
            env["config"],
            adapter,
            adapter,
            epic_id=eid,
            log_path=env["log_path"],
        )

        assert final_status == "human-gate"
        assert len(adapter.calls) == 4
        assert _get_epic_status(env["conn"], eid) == "human-gate"

    def test_revision_prompt_preserves_ticket_targeted_findings(self, project_env):
        env = project_env
        eid = _add_epic(env)

        adapter = MockPlanningAdapter(
            results=[
                _make_draft_success(),
                RunResult(
                    run_id="mock",
                    exit_status="success",
                    duration_seconds=1.0,
                    raw_stdout="ok",
                    raw_stderr="",
                    structured_result=PlanningReviewResult(
                        verdict="fail",
                        confidence="high",
                        findings=[
                            PlanningFinding(
                                severity="blocking",
                                category="completeness",
                                description="Missing validation case",
                                target_type="ticket",
                                target_sequence=2,
                            )
                        ],
                        scope_reviewed=PlanningScopeReviewed(
                            epic_reviewed=True,
                            tickets_reviewed=[1, 2],
                        ),
                    ),
                ),
                _make_draft_success(),
                _make_review_pass(),
            ]
        )

        final_status, _ = run_planning_loop(
            env["conn"],
            env["project_id"],
            env["config"],
            adapter,
            adapter,
            epic_id=eid,
            log_path=env["log_path"],
        )

        assert final_status == "human-gate"
        assert "ticket #2" in adapter.calls[2].prompt

    def test_review_keeps_distinct_ticket_findings_with_same_description(
        self, project_env
    ):
        env = project_env
        eid = _add_epic(env)

        adapter = MockPlanningAdapter(
            results=[
                RunResult(
                    run_id="mock",
                    exit_status="success",
                    duration_seconds=1.0,
                    raw_stdout="ok",
                    raw_stderr="",
                    structured_result=PlanningReviewResult(
                        verdict="fail",
                        confidence="high",
                        findings=[
                            PlanningFinding(
                                severity="blocking",
                                category="completeness",
                                description="Missing validation case",
                                target_type="ticket",
                                target_sequence=1,
                            ),
                            PlanningFinding(
                                severity="blocking",
                                category="completeness",
                                description="Missing validation case",
                                target_type="ticket",
                                target_sequence=2,
                            ),
                        ],
                        scope_reviewed=PlanningScopeReviewed(
                            epic_reviewed=True,
                            tickets_reviewed=[1, 2],
                        ),
                    ),
                ),
            ]
        )

        draft_status = run_draft_pipeline(
            env["conn"],
            env["project_id"],
            {"id": eid, "status": "new"},
            env["config"],
            MockPlanningAdapter(results=[_make_draft_success()]),
            log_path=env["log_path"],
        )
        assert draft_status == "in-review"

        final_status = run_planning_review_pipeline(
            env["conn"],
            env["project_id"],
            {"id": eid},
            env["config"],
            adapter,
            log_path=env["log_path"],
        )

        assert final_status == "revise"
        findings = env["conn"].execute(
            "SELECT fingerprint, planned_ticket_id FROM planning_findings "
            "WHERE epic_id = ? AND disposition = 'open' ORDER BY fingerprint",
            (eid,),
        ).fetchall()
        assert len(findings) == 2
        assert findings[0]["planned_ticket_id"] != findings[1]["planned_ticket_id"]


# ---------------------------------------------------------------------------
# Cycle limit gate
# ---------------------------------------------------------------------------


class TestPlanningLoopCycleLimit:
    def test_stops_at_cycle_limit(self, project_env):
        env = project_env
        eid = _add_epic(env)

        # max_cycles=1: after first review fail, the revise->draft path
        # detects cycle limit and goes to human-gate
        adapter = MockPlanningAdapter(
            results=[
                _make_draft_success(),
                _make_review_fail(),
            ]
        )
        final_status, detail = run_planning_loop(
            env["conn"],
            env["project_id"],
            env["config"],
            adapter,
            adapter,
            epic_id=eid,
            max_cycles=1,
            log_path=env["log_path"],
        )

        assert final_status == "human-gate"
        assert "cycle_limit" in detail


# ---------------------------------------------------------------------------
# Retry limit block
# ---------------------------------------------------------------------------


class TestPlanningLoopBlocked:
    def test_blocks_on_repeated_draft_failure(self, project_env):
        env = project_env
        eid = _add_epic(env)

        # All draft runs fail — should hit retry limit and block
        adapter = MockPlanningAdapter(
            results=[
                _make_draft_failure(),
                _make_draft_failure(),
                _make_draft_failure(),
            ]
        )
        final_status, detail = run_planning_loop(
            env["conn"],
            env["project_id"],
            env["config"],
            adapter,
            adapter,
            epic_id=eid,
            log_path=env["log_path"],
        )

        assert final_status == "blocked"
        assert "blocked" in detail.lower()
        assert _get_epic_status(env["conn"], eid) == "blocked"

    def test_escalate_to_human_gate(self, project_env):
        env = project_env
        eid = _add_epic(env)

        adapter = MockPlanningAdapter(
            results=[_make_draft_success(), _make_review_escalate()]
        )
        final_status, detail = run_planning_loop(
            env["conn"],
            env["project_id"],
            env["config"],
            adapter,
            adapter,
            epic_id=eid,
            log_path=env["log_path"],
        )

        assert final_status == "human-gate"
        assert "reviewer_escalated" in detail


# ---------------------------------------------------------------------------
# DB state consistency
# ---------------------------------------------------------------------------


class TestPlanningLoopDbState:
    def test_orchestrator_state_consistent(self, project_env):
        env = project_env
        eid = _add_epic(env)

        adapter = MockPlanningAdapter(
            results=[_make_draft_success(), _make_review_pass()]
        )
        run_planning_loop(
            env["conn"],
            env["project_id"],
            env["config"],
            adapter,
            adapter,
            epic_id=eid,
            log_path=env["log_path"],
        )

        state = get_state(env["conn"], env["project_id"])
        assert state["status"] == "awaiting_human"
        assert state["loop_type"] == "planning"
        assert state["active_plan_id"] == eid

    def test_state_transitions_recorded(self, project_env):
        env = project_env
        eid = _add_epic(env)

        adapter = MockPlanningAdapter(
            results=[_make_draft_success(), _make_review_pass()]
        )
        run_planning_loop(
            env["conn"],
            env["project_id"],
            env["config"],
            adapter,
            adapter,
            epic_id=eid,
            log_path=env["log_path"],
        )

        transitions = (
            env["conn"]
            .execute(
                "SELECT from_status, to_status FROM state_transitions "
                "WHERE epic_id = ? ORDER BY id",
                (eid,),
            )
            .fetchall()
        )
        statuses = [(t["from_status"], t["to_status"]) for t in transitions]
        assert ("new", "drafting") in statuses
        assert ("drafting", "in-review") in statuses
        assert ("in-review", "human-gate") in statuses

    def test_agent_runs_recorded(self, project_env):
        env = project_env
        eid = _add_epic(env)

        adapter = MockPlanningAdapter(
            results=[_make_draft_success(), _make_review_pass()]
        )
        run_planning_loop(
            env["conn"],
            env["project_id"],
            env["config"],
            adapter,
            adapter,
            epic_id=eid,
            log_path=env["log_path"],
        )

        runs = (
            env["conn"]
            .execute(
                "SELECT role, exit_status FROM agent_runs "
                "WHERE epic_id = ? ORDER BY started_at",
                (eid,),
            )
            .fetchall()
        )
        roles = [r["role"] for r in runs]
        assert "planner" in roles
        assert "reviewer" in roles

    def test_planned_tickets_persisted(self, project_env):
        env = project_env
        eid = _add_epic(env)

        adapter = MockPlanningAdapter(
            results=[_make_draft_success(), _make_review_pass()]
        )
        run_planning_loop(
            env["conn"],
            env["project_id"],
            env["config"],
            adapter,
            adapter,
            epic_id=eid,
            log_path=env["log_path"],
        )

        tickets = (
            env["conn"]
            .execute(
                "SELECT title, sequence FROM planned_tickets "
                "WHERE epic_id = ? ORDER BY sequence",
                (eid,),
            )
            .fetchall()
        )
        assert len(tickets) == 2
        assert tickets[0]["title"] == "Data model"
        assert tickets[1]["title"] == "API endpoints"


# ---------------------------------------------------------------------------
# Activity log
# ---------------------------------------------------------------------------


class TestPlanningLoopActivityLog:
    def test_loop_events_logged(self, project_env):
        env = project_env
        eid = _add_epic(env)

        adapter = MockPlanningAdapter(
            results=[_make_draft_success(), _make_review_pass()]
        )
        run_planning_loop(
            env["conn"],
            env["project_id"],
            env["config"],
            adapter,
            adapter,
            epic_id=eid,
            log_path=env["log_path"],
        )

        log_content = env["log_path"].read_text()
        assert "PLANNING_LOOP_START" in log_content
        assert "PLANNING_LOOP_STOP" in log_content
        assert "RUN_START" in log_content
        assert "RUN_END" in log_content


# ---------------------------------------------------------------------------
# Resume: interrupted planning run
# ---------------------------------------------------------------------------


class TestPlanningResume:
    def test_interrupted_planner_run_marked_failure(self, project_env):
        """An interrupted planner run is marked as failure and epic can retry."""
        env = project_env
        eid = _add_epic(env)

        # Start a draft that will leave the orchestrator in 'running' state
        # by simulating an interruption: manually create a run record and
        # set orchestrator state
        from capsaicin.orchestrator import start_planning_run
        from capsaicin.queries import generate_id, now_utc
        from capsaicin.state_machine import transition_planned_epic

        # Transition to drafting and init cycle
        transition_planned_epic(env["conn"], eid, "drafting", "system", reason="test")
        from capsaicin.orchestrator import init_planning_cycle

        init_planning_cycle(env["conn"], eid)

        # Create a running planner run
        run_id = generate_id()
        env["conn"].execute(
            "INSERT INTO agent_runs "
            "(id, epic_id, role, mode, cycle_number, attempt_number, "
            "exit_status, prompt, run_request, started_at) "
            "VALUES (?, ?, 'planner', 'read-write', 1, 1, 'running', 'test', '{}', ?)",
            (run_id, eid, now_utc()),
        )
        env["conn"].commit()

        # Set orchestrator to running with planning context
        start_planning_run(env["conn"], env["project_id"], eid, run_id)

        # Now resume
        from capsaicin.resume import resume_pipeline
        from capsaicin.adapters.claude_code import ClaudeCodeAdapter

        # Use mock adapters for resume
        class NoOpAdapter:
            def execute(self, request):
                return RunResult(
                    run_id="mock",
                    exit_status="success",
                    duration_seconds=0.0,
                    raw_stdout="",
                    raw_stderr="",
                )

        action, detail = resume_pipeline(
            env["conn"],
            env["project_id"],
            env["config"],
            NoOpAdapter(),
            NoOpAdapter(),
            log_path=env["log_path"],
        )

        assert action == "interrupted"
        assert "planner" in detail
        assert "marked as failure" in detail

        # The run should now be marked as failure
        run_row = (
            env["conn"]
            .execute("SELECT exit_status FROM agent_runs WHERE id = ?", (run_id,))
            .fetchone()
        )
        assert run_row["exit_status"] == "failure"

    def test_interrupted_planning_reviewer_blocked_at_limit(self, project_env):
        """An interrupted reviewer run at retry limit blocks the epic."""
        env = project_env
        eid = _add_epic(env)

        from capsaicin.orchestrator import (
            init_planning_cycle,
            start_planning_run,
        )
        from capsaicin.queries import generate_id, now_utc
        from capsaicin.state_machine import transition_planned_epic

        # Move epic to in-review
        transition_planned_epic(env["conn"], eid, "drafting", "system", reason="test")
        transition_planned_epic(env["conn"], eid, "in-review", "system", reason="test")
        init_planning_cycle(env["conn"], eid)

        # Set review attempt to the limit (max_review_retries=2)
        env["conn"].execute(
            "UPDATE planned_epics SET current_review_attempt = 2 WHERE id = ?",
            (eid,),
        )
        env["conn"].commit()

        # Create a running reviewer run
        run_id = generate_id()
        env["conn"].execute(
            "INSERT INTO agent_runs "
            "(id, epic_id, role, mode, cycle_number, attempt_number, "
            "exit_status, prompt, run_request, started_at) "
            "VALUES (?, ?, 'reviewer', 'read-only', 1, 2, 'running', 'test', '{}', ?)",
            (run_id, eid, now_utc()),
        )
        env["conn"].commit()

        start_planning_run(env["conn"], env["project_id"], eid, run_id)

        from capsaicin.resume import resume_pipeline

        class NoOpAdapter:
            def execute(self, request):
                return RunResult(
                    run_id="mock",
                    exit_status="success",
                    duration_seconds=0.0,
                    raw_stdout="",
                    raw_stderr="",
                )

        action, detail = resume_pipeline(
            env["conn"],
            env["project_id"],
            env["config"],
            NoOpAdapter(),
            NoOpAdapter(),
            log_path=env["log_path"],
        )

        assert action == "interrupted"
        assert "blocked" in detail
        assert _get_epic_status(env["conn"], eid) == "blocked"
