"""Tests for backend evidence injection into prompts and suggested-requirement
persistence (T07)."""

from __future__ import annotations

import json

from capsaicin.adapters.types import (
    AcceptanceCriterion,
    BackendEvidence,
    EvidenceRequirement,
    Finding,
    PlannedAcceptanceCriterion,
    PlannedEpicData,
    PlannedTicketData,
    PlannerResult,
    PlanningFinding,
    PlanningReviewResult,
    PlanningScopeReviewed,
    RunResult,
    SuggestedEvidenceRequirement,
)
from capsaicin.prompts import (
    _format_evidence,
    build_implementer_prompt,
    build_planner_draft_prompt,
    build_planner_revise_prompt,
    build_planning_reviewer_prompt,
    build_reviewer_prompt,
)


# ---------------------------------------------------------------------------
# Evidence fixtures
# ---------------------------------------------------------------------------


def _command_evidence():
    return BackendEvidence(
        id="ev-1",
        epic_id="epic-1",
        evidence_type="command",
        title="codex exec probe",
        command='codex exec --json --sandbox read-only --ephemeral "Reply with hi"',
        body="Ran in test environment.",
    )


def _output_evidence():
    return BackendEvidence(
        id="ev-2",
        epic_id="epic-1",
        evidence_type="output_envelope",
        title="codex exec output",
        command='codex exec --json "hi"',
        stdout='{"type":"item.completed","item":{"text":"hi"}}',
        stderr="warning: beta feature",
    )


def _structured_evidence():
    return BackendEvidence(
        id="ev-3",
        epic_id="epic-1",
        evidence_type="structured_result",
        title="Codex structured output",
        structured_data={"verdict": "pass", "confidence": "high"},
        command="codex exec --json --output-schema schema.json",
    )


def _permission_denial_evidence():
    return BackendEvidence(
        id="ev-4",
        epic_id="epic-1",
        evidence_type="permission_denial",
        title="Write denied in read-only sandbox",
        command='codex exec --sandbox read-only "create file"',
        body="Write was refused.",
        stdout="Operation not permitted",
        stderr="EPERM: operation not permitted, open '/tmp/out'",
    )


def _behavioral_note_evidence():
    return BackendEvidence(
        id="ev-5",
        epic_id="epic-1",
        evidence_type="behavioral_note",
        title="Websocket fallback",
        body="Codex attempted websocket first then fell back to HTTPS.",
    )


def _all_evidence():
    return [
        _command_evidence(),
        _output_evidence(),
        _structured_evidence(),
        _permission_denial_evidence(),
        _behavioral_note_evidence(),
    ]


# ---------------------------------------------------------------------------
# _format_evidence
# ---------------------------------------------------------------------------


class TestFormatEvidence:
    def test_empty_evidence_returns_empty(self):
        assert _format_evidence([]) == ""

    def test_command_evidence_renders_code_block(self):
        result = _format_evidence([_command_evidence()])
        assert "```bash" in result
        assert "codex exec" in result
        assert "codex exec probe" in result
        assert "Ran in test environment." in result

    def test_output_envelope_renders_stdout_and_stderr(self):
        result = _format_evidence([_output_evidence()])
        assert "**stdout:**" in result
        assert "item.completed" in result
        assert "**stderr:**" in result
        assert "warning: beta feature" in result

    def test_structured_result_renders_json(self):
        result = _format_evidence([_structured_evidence()])
        assert "```json" in result
        assert '"verdict": "pass"' in result

    def test_permission_denial_renders_stderr(self):
        result = _format_evidence([_permission_denial_evidence()])
        assert "Denied command:" in result
        assert "**stdout:**" in result
        assert "**stderr:**" in result
        assert "EPERM" in result

    def test_behavioral_note_renders_as_quote(self):
        result = _format_evidence([_behavioral_note_evidence()])
        assert "> Codex attempted websocket" in result

    def test_multiple_evidence_all_present(self):
        result = _format_evidence(_all_evidence())
        assert "codex exec probe" in result
        assert "codex exec output" in result
        assert "Codex structured output" in result
        assert "Write denied" in result
        assert "Websocket fallback" in result


# ---------------------------------------------------------------------------
# Implementer prompt evidence
# ---------------------------------------------------------------------------


def _ticket(**overrides):
    defaults = {
        "title": "Add codex adapter",
        "description": "Implement codex CLI adapter.",
    }
    defaults.update(overrides)
    return defaults


def _criteria():
    return [
        AcceptanceCriterion(id="ac-1", description="Adapter exists", status="pending"),
    ]


class TestImplementerPromptEvidence:
    def test_evidence_included_when_provided(self):
        prompt = build_implementer_prompt(
            _ticket(),
            _criteria(),
            [],
            1,
            3,
            evidence=[_command_evidence(), _output_evidence()],
        )
        assert "Backend Context" in prompt
        assert "codex exec probe" in prompt
        assert "item.completed" in prompt

    def test_evidence_omitted_when_none(self):
        prompt = build_implementer_prompt(_ticket(), _criteria(), [], 1, 3)
        assert "Backend Context" not in prompt

    def test_evidence_omitted_when_empty(self):
        prompt = build_implementer_prompt(
            _ticket(),
            _criteria(),
            [],
            1,
            3,
            evidence=[],
        )
        assert "Backend Context" not in prompt


# ---------------------------------------------------------------------------
# Reviewer prompt evidence
# ---------------------------------------------------------------------------


class TestReviewerPromptEvidence:
    def test_evidence_included_when_provided(self):
        prompt = build_reviewer_prompt(
            _ticket(),
            _criteria(),
            "diff",
            [],
            evidence=[_output_evidence()],
        )
        assert "Backend Context" in prompt
        assert "codex exec output" in prompt

    def test_evidence_omitted_when_none(self):
        prompt = build_reviewer_prompt(_ticket(), _criteria(), "diff", [])
        assert "Backend Context" not in prompt

    def test_evidence_appears_before_output_format(self):
        prompt = build_reviewer_prompt(
            _ticket(),
            _criteria(),
            "diff",
            [],
            evidence=[_command_evidence()],
        )
        ctx_pos = prompt.index("Backend Context")
        fmt_pos = prompt.index("Output Format")
        assert ctx_pos < fmt_pos


# ---------------------------------------------------------------------------
# Planner draft prompt evidence
# ---------------------------------------------------------------------------


class TestPlannerDraftPromptEvidence:
    def test_evidence_included_when_provided(self):
        prompt = build_planner_draft_prompt(
            "Add codex support",
            evidence=[_command_evidence(), _behavioral_note_evidence()],
        )
        assert "Backend Validation Evidence" in prompt
        assert "codex exec probe" in prompt
        assert "Websocket fallback" in prompt

    def test_evidence_omitted_when_none(self):
        prompt = build_planner_draft_prompt("Add codex support")
        assert "Backend Validation Evidence" not in prompt

    def test_suggests_evidence_requirement_instruction(self):
        prompt = build_planner_draft_prompt("Add codex support")
        assert "suggested_evidence_requirements" in prompt
        assert "suggest evidence requirements" in prompt.lower()


# ---------------------------------------------------------------------------
# Planner revise prompt evidence
# ---------------------------------------------------------------------------


class TestPlannerRevisePromptEvidence:
    def test_evidence_included_when_provided(self):
        prompt = build_planner_revise_prompt(
            problem_statement="Add codex support",
            plan_draft={
                "epic": {"title": "E", "summary": "S", "success_outcome": "O"},
                "tickets": [],
            },
            prior_findings=[],
            cycle=2,
            max_cycles=3,
            evidence=[_structured_evidence()],
        )
        assert "Backend Validation Evidence" in prompt
        assert '"verdict": "pass"' in prompt

    def test_evidence_omitted_when_none(self):
        prompt = build_planner_revise_prompt(
            problem_statement="Add codex support",
            plan_draft={
                "epic": {"title": "E", "summary": "S", "success_outcome": "O"},
                "tickets": [],
            },
            prior_findings=[],
            cycle=2,
            max_cycles=3,
        )
        assert "Backend Validation Evidence" not in prompt


# ---------------------------------------------------------------------------
# Planning reviewer prompt evidence
# ---------------------------------------------------------------------------


class TestPlanningReviewerPromptEvidence:
    def test_evidence_included_when_provided(self):
        prompt = build_planning_reviewer_prompt(
            problem_statement="Add codex support",
            plan_draft={
                "epic": {"title": "E", "summary": "S", "success_outcome": "O"},
                "tickets": [],
            },
            evidence=[_permission_denial_evidence()],
        )
        assert "Backend Validation Evidence" in prompt
        assert "Write denied" in prompt
        assert "EPERM" in prompt

    def test_evidence_omitted_when_none(self):
        prompt = build_planning_reviewer_prompt(
            problem_statement="Add codex support",
            plan_draft={
                "epic": {"title": "E", "summary": "S", "success_outcome": "O"},
                "tickets": [],
            },
        )
        assert "Backend Validation Evidence" not in prompt

    def test_evidence_appears_before_output_format(self):
        prompt = build_planning_reviewer_prompt(
            problem_statement="Add codex support",
            plan_draft={
                "epic": {"title": "E", "summary": "S", "success_outcome": "O"},
                "tickets": [],
            },
            evidence=[_command_evidence()],
        )
        ev_pos = prompt.index("Backend Validation Evidence")
        fmt_pos = prompt.index("Output Format")
        assert ev_pos < fmt_pos


# ---------------------------------------------------------------------------
# PlannerResult suggested_evidence_requirements round-trip
# ---------------------------------------------------------------------------


class TestSuggestedEvidenceRequirementsType:
    def test_round_trip(self):
        result = PlannerResult(
            epic=PlannedEpicData(title="E", summary="S", success_outcome="O"),
            tickets=[
                PlannedTicketData(
                    sequence=1,
                    title="T",
                    goal="G",
                    acceptance_criteria=[PlannedAcceptanceCriterion(description="C")],
                ),
            ],
            suggested_evidence_requirements=[
                SuggestedEvidenceRequirement(
                    description="Check codex exec output",
                    suggested_command='codex exec --json "hi"',
                ),
            ],
        )
        d = result.to_dict()
        assert len(d["suggested_evidence_requirements"]) == 1
        assert (
            d["suggested_evidence_requirements"][0]["suggested_command"]
            == 'codex exec --json "hi"'
        )

        restored = PlannerResult.from_dict(d)
        assert len(restored.suggested_evidence_requirements) == 1
        assert (
            restored.suggested_evidence_requirements[0].description
            == "Check codex exec output"
        )

    def test_empty_by_default(self):
        result = PlannerResult(
            epic=PlannedEpicData(title="E", summary="S", success_outcome="O"),
            tickets=[
                PlannedTicketData(
                    sequence=1,
                    title="T",
                    goal="G",
                    acceptance_criteria=[PlannedAcceptanceCriterion(description="C")],
                ),
            ],
        )
        assert result.suggested_evidence_requirements == []
        d = result.to_dict()
        assert "suggested_evidence_requirements" not in d

    def test_from_dict_without_field(self):
        d = {
            "epic": {"title": "E", "summary": "S", "success_outcome": "O"},
            "tickets": [
                {
                    "sequence": 1,
                    "title": "T",
                    "goal": "G",
                    "scope": [],
                    "non_goals": [],
                    "acceptance_criteria": [{"description": "C"}],
                    "dependencies": [],
                    "references": [],
                    "implementation_notes": [],
                }
            ],
        }
        result = PlannerResult.from_dict(d)
        assert result.suggested_evidence_requirements == []


# ---------------------------------------------------------------------------
# Suggested-requirement persistence and de-duplication (integration)
# ---------------------------------------------------------------------------


def _make_planner_result_with_suggestions(suggestions):
    """Create a PlannerResult with given suggested_evidence_requirements."""
    return PlannerResult(
        epic=PlannedEpicData(
            title="Codex Adapter",
            summary="Add codex adapter",
            success_outcome="Working adapter",
        ),
        tickets=[
            PlannedTicketData(
                sequence=1,
                title="Adapter impl",
                goal="Implement adapter",
                scope=["Create adapter class"],
                non_goals=["Tests"],
                acceptance_criteria=[
                    PlannedAcceptanceCriterion(description="Adapter exists")
                ],
                dependencies=[],
                references=["src/adapters/base.py"],
                implementation_notes=["Follow claude_code.py"],
            ),
        ],
        suggested_evidence_requirements=suggestions,
    )


def _make_draft_success(suggestions=None):
    return RunResult(
        run_id="mock",
        exit_status="success",
        duration_seconds=1.0,
        raw_stdout="ok",
        raw_stderr="",
        structured_result=_make_planner_result_with_suggestions(suggestions or []),
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
                tickets_reviewed=[1],
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
                    description="Missing test ticket",
                    target_type="epic",
                )
            ],
            scope_reviewed=PlanningScopeReviewed(
                epic_reviewed=True,
                tickets_reviewed=[1],
            ),
        ),
    )


def _add_epic(env, problem="Add codex adapter"):
    from capsaicin.app.commands.new_epic import new_epic

    result = new_epic(
        conn=env["conn"],
        project_id=env["project_id"],
        problem_statement=problem,
        log_path=env["log_path"],
    )
    return result.epic_id


def _get_pending_requirements(conn, epic_id):
    rows = conn.execute(
        "SELECT description, suggested_command FROM evidence_requirements "
        "WHERE epic_id = ? AND status = 'pending' "
        "ORDER BY description",
        (epic_id,),
    ).fetchall()
    return [(r["description"], r["suggested_command"]) for r in rows]


class MockPlanningAdapter:
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
        return RunResult(
            run_id="mock",
            exit_status="success",
            duration_seconds=1.0,
            raw_stdout="ok",
            raw_stderr="",
        )


class TestSuggestedRequirementPersistence:
    def test_suggestions_persisted_on_draft(self, project_env):
        """Suggested requirements from a successful draft are persisted."""
        env = project_env
        epic_id = _add_epic(env)

        suggestions = [
            SuggestedEvidenceRequirement(
                description="Check codex --help",
                suggested_command="codex --help",
            ),
            SuggestedEvidenceRequirement(
                description="Probe exec output",
                suggested_command='codex exec --json "hi"',
            ),
        ]
        adapter = MockPlanningAdapter(results=[_make_draft_success(suggestions)])

        from capsaicin.planning_run import run_draft_pipeline, select_epic_for_draft

        epic = select_epic_for_draft(env["conn"], env["project_id"], epic_id)
        run_draft_pipeline(
            env["conn"],
            env["project_id"],
            epic,
            env["config"],
            adapter,
            log_path=env["log_path"],
        )

        reqs = _get_pending_requirements(env["conn"], epic_id)
        assert len(reqs) == 2
        assert ("Check codex --help", "codex --help") in reqs
        assert ("Probe exec output", 'codex exec --json "hi"') in reqs

    def test_duplicates_not_created_across_cycles(self, project_env):
        """When the planner suggests the same requirement on a second draft,
        no duplicate rows are created."""
        env = project_env
        epic_id = _add_epic(env)

        suggestions = [
            SuggestedEvidenceRequirement(
                description="Check codex --help",
                suggested_command="codex --help",
            ),
        ]

        # Cycle 1: draft (with suggestion) -> review fail
        # Cycle 2: revised draft (same suggestion) -> review pass
        adapter = MockPlanningAdapter(
            results=[
                _make_draft_success(suggestions),
                _make_review_fail(),
                _make_draft_success(suggestions),
                _make_review_pass(),
            ]
        )

        from capsaicin.planning_loop import run_planning_loop

        run_planning_loop(
            env["conn"],
            env["project_id"],
            env["config"],
            epic_id=epic_id,
            draft_adapter=adapter,
            review_adapter=adapter,
            log_path=env["log_path"],
            max_cycles=3,
        )

        reqs = _get_pending_requirements(env["conn"], epic_id)
        assert len(reqs) == 1
        assert reqs[0] == ("Check codex --help", "codex --help")

    def test_different_suggestions_across_cycles_all_kept(self, project_env):
        """When the planner suggests different requirements on different cycles,
        all unique requirements are kept."""
        env = project_env
        epic_id = _add_epic(env)

        suggestions_cycle1 = [
            SuggestedEvidenceRequirement(
                description="Check codex --help",
                suggested_command="codex --help",
            ),
        ]
        suggestions_cycle2 = [
            SuggestedEvidenceRequirement(
                description="Probe exec mode",
                suggested_command='codex exec --json "test"',
            ),
        ]

        adapter = MockPlanningAdapter(
            results=[
                _make_draft_success(suggestions_cycle1),
                _make_review_fail(),
                _make_draft_success(suggestions_cycle2),
                _make_review_pass(),
            ]
        )

        from capsaicin.planning_loop import run_planning_loop

        run_planning_loop(
            env["conn"],
            env["project_id"],
            env["config"],
            epic_id=epic_id,
            draft_adapter=adapter,
            review_adapter=adapter,
            log_path=env["log_path"],
            max_cycles=3,
        )

        reqs = _get_pending_requirements(env["conn"], epic_id)
        assert len(reqs) == 2
        descs = {r[0] for r in reqs}
        assert "Check codex --help" in descs
        assert "Probe exec mode" in descs

    def test_no_suggestions_creates_no_requirements(self, project_env):
        """A planner result with no suggested requirements creates no rows."""
        env = project_env
        epic_id = _add_epic(env)

        adapter = MockPlanningAdapter(results=[_make_draft_success()])

        from capsaicin.planning_run import run_draft_pipeline, select_epic_for_draft

        epic = select_epic_for_draft(env["conn"], env["project_id"], epic_id)
        run_draft_pipeline(
            env["conn"],
            env["project_id"],
            epic,
            env["config"],
            adapter,
            log_path=env["log_path"],
        )

        reqs = _get_pending_requirements(env["conn"], epic_id)
        assert len(reqs) == 0
