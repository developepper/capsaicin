"""Tests for prompt assembly (T11)."""

from __future__ import annotations

from capsaicin.adapters.types import AcceptanceCriterion, Finding
from capsaicin.prompts import build_implementer_prompt, build_reviewer_prompt


def _ticket(**overrides):
    defaults = {"title": "Add auth middleware", "description": "Implement JWT auth."}
    defaults.update(overrides)
    return defaults


def _criteria():
    return [
        AcceptanceCriterion(
            id="ac-1", description="Login returns JWT", status="pending"
        ),
        AcceptanceCriterion(
            id="ac-2", description="Expired tokens rejected", status="met"
        ),
    ]


def _findings():
    return [
        Finding(
            severity="blocking",
            category="correctness",
            description="Missing null check on token",
            location="src/auth.py:42",
            acceptance_criterion_id="ac-1",
        ),
        Finding(
            severity="warning",
            category="style",
            description="Inconsistent naming",
            disposition="open",
        ),
    ]


# ---------------------------------------------------------------------------
# Implementer prompt
# ---------------------------------------------------------------------------


class TestBuildImplementerPrompt:
    def test_contains_role_instruction(self):
        prompt = build_implementer_prompt(_ticket(), _criteria(), [], 1, 3)
        assert "implementer" in prompt.lower()
        assert "Role" in prompt

    def test_contains_scope_constraint(self):
        prompt = build_implementer_prompt(_ticket(), _criteria(), [], 1, 3)
        assert "Scope Constraint" in prompt
        assert "Only make changes" in prompt

    def test_contains_ticket_title(self):
        prompt = build_implementer_prompt(_ticket(), _criteria(), [], 1, 3)
        assert "Add auth middleware" in prompt

    def test_contains_ticket_description(self):
        prompt = build_implementer_prompt(_ticket(), _criteria(), [], 1, 3)
        assert "Implement JWT auth." in prompt

    def test_contains_criteria_with_statuses(self):
        prompt = build_implementer_prompt(_ticket(), _criteria(), [], 1, 3)
        assert "[pending]" in prompt
        assert "[met]" in prompt
        assert "Login returns JWT" in prompt
        assert "Expired tokens rejected" in prompt
        assert "ac-1" in prompt
        assert "ac-2" in prompt

    def test_contains_cycle_info(self):
        prompt = build_implementer_prompt(_ticket(), _criteria(), [], 2, 3)
        assert "cycle 2 of 3" in prompt

    def test_prior_findings_included_when_present(self):
        prompt = build_implementer_prompt(_ticket(), _criteria(), _findings(), 2, 3)
        assert "Prior Findings" in prompt
        assert "Missing null check" in prompt
        assert "[blocking]" in prompt
        assert "src/auth.py:42" in prompt
        assert "criterion: ac-1" in prompt

    def test_prior_findings_omitted_when_empty(self):
        prompt = build_implementer_prompt(_ticket(), _criteria(), [], 1, 3)
        assert "Prior Findings" not in prompt

    def test_no_criteria(self):
        prompt = build_implementer_prompt(_ticket(), [], [], 1, 3)
        assert "No acceptance criteria defined" in prompt


# ---------------------------------------------------------------------------
# Reviewer prompt
# ---------------------------------------------------------------------------


class TestBuildReviewerPrompt:
    def test_contains_reviewer_role_instruction(self):
        prompt = build_reviewer_prompt(_ticket(), _criteria(), "diff", [])
        assert "independent" in prompt.lower()
        assert "reviewer" in prompt.lower()
        assert "quality gate" in prompt.lower()

    def test_contains_anti_bias_instruction(self):
        prompt = build_reviewer_prompt(_ticket(), _criteria(), "diff", [])
        assert "Anti-Bias" in prompt
        assert "commit messages" in prompt.lower()
        assert "inline" in prompt.lower()

    def test_contains_ticket_title(self):
        prompt = build_reviewer_prompt(_ticket(), _criteria(), "diff", [])
        assert "Add auth middleware" in prompt

    def test_contains_ticket_description(self):
        prompt = build_reviewer_prompt(_ticket(), _criteria(), "diff", [])
        assert "Implement JWT auth." in prompt

    def test_contains_criteria(self):
        prompt = build_reviewer_prompt(_ticket(), _criteria(), "diff", [])
        assert "Login returns JWT" in prompt
        assert "Expired tokens rejected" in prompt
        assert "ac-1" in prompt

    def test_contains_diff(self):
        diff = "--- a/foo.py\n+++ b/foo.py\n@@ -1 +1 @@\n-old\n+new"
        prompt = build_reviewer_prompt(_ticket(), _criteria(), diff, [])
        assert "```diff" in prompt
        assert diff in prompt

    def test_contains_json_schema(self):
        prompt = build_reviewer_prompt(_ticket(), _criteria(), "diff", [])
        # Field names are present
        assert '"verdict"' in prompt
        assert '"confidence"' in prompt
        assert '"findings"' in prompt
        assert '"scope_reviewed"' in prompt
        assert '"files_examined"' in prompt
        assert '"criteria_checked"' in prompt
        assert '"criterion_id"' in prompt
        assert '"acceptance_criterion_id"' in prompt

    def test_schema_is_valid_json_schema(self):
        """The embedded schema must be a real JSON Schema, not just an example payload."""
        from capsaicin.prompts import REVIEW_RESULT_SCHEMA

        assert REVIEW_RESULT_SCHEMA["type"] == "object"
        assert "properties" in REVIEW_RESULT_SCHEMA
        assert "required" in REVIEW_RESULT_SCHEMA

        props = REVIEW_RESULT_SCHEMA["properties"]

        # verdict has enum constraint
        assert props["verdict"]["type"] == "string"
        assert set(props["verdict"]["enum"]) == {"pass", "fail", "escalate"}

        # confidence has enum constraint
        assert props["confidence"]["type"] == "string"
        assert set(props["confidence"]["enum"]) == {"high", "medium", "low"}

        # findings is an array with item schema
        assert props["findings"]["type"] == "array"
        finding_props = props["findings"]["items"]["properties"]
        assert set(finding_props["severity"]["enum"]) == {"blocking", "warning", "info"}
        assert set(finding_props["disposition"]["enum"]) == {
            "open",
            "fixed",
            "wont_fix",
            "disputed",
        }
        assert "description" in finding_props
        assert "category" in finding_props
        assert "acceptance_criterion_id" in finding_props

        # scope_reviewed has nested structure
        sr_props = props["scope_reviewed"]["properties"]
        assert sr_props["files_examined"]["type"] == "array"
        assert sr_props["tests_run"]["type"] == "boolean"
        cc_props = sr_props["criteria_checked"]["items"]["properties"]
        assert "criterion_id" in cc_props
        assert "description" in cc_props

    def test_schema_embedded_in_prompt(self):
        """The prompt must contain the schema as a JSON block, not an example payload."""
        prompt = build_reviewer_prompt(_ticket(), _criteria(), "diff", [])
        assert '"type": "object"' in prompt
        assert '"enum"' in prompt
        assert '"required"' in prompt

    def test_contains_schema_rules(self):
        prompt = build_reviewer_prompt(_ticket(), _criteria(), "diff", [])
        assert "verdict: fail" in prompt
        assert "verdict: pass" in prompt
        assert "verdict: escalate" in prompt

    def test_prior_findings_included_when_present(self):
        prompt = build_reviewer_prompt(_ticket(), _criteria(), "diff", _findings())
        assert "Prior Findings" in prompt
        assert "Missing null check" in prompt
        assert "[blocking]" in prompt
        assert "addressed" in prompt.lower()

    def test_prior_findings_omitted_when_empty(self):
        prompt = build_reviewer_prompt(_ticket(), _criteria(), "diff", [])
        assert "Prior Findings" not in prompt

    def test_findings_show_dispositions(self):
        prompt = build_reviewer_prompt(_ticket(), _criteria(), "diff", _findings())
        assert "[open]" in prompt
