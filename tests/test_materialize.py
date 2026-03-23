"""Tests for plan materialization (T05).

Covers:
- doc generation under docs/tickets/generated/<slug>/
- implementation-ticket DB record creation with lineage
- hash-gated regeneration protecting manual edits
- approve triggers materialization as side-effect
"""

from __future__ import annotations

import json

import pytest

from capsaicin.app.commands.approve_epic import approve
from capsaicin.app.commands.new_epic import new_epic
from capsaicin.db import get_connection, run_migrations
from capsaicin.materialize import (
    MaterializationResult,
    _slugify,
    materialize_epic,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def conn():
    c = get_connection(":memory:")
    run_migrations(c)
    c.execute(
        "INSERT INTO projects (id, name, repo_path) VALUES (?, ?, ?)",
        ("p1", "test", "/tmp/repo"),
    )
    c.commit()
    yield c
    c.close()


def _create_approved_epic(conn, title="Build Auth System", num_tickets=2):
    """Create an approved epic with planned tickets, criteria, and deps."""
    from capsaicin.queries import generate_id

    epic_id = generate_id()
    conn.execute(
        "INSERT INTO planned_epics "
        "(id, project_id, problem_statement, title, summary, "
        "success_outcome, sequencing_notes, status) "
        "VALUES (?, 'p1', 'Need auth', ?, 'Add authentication', "
        "'Users can log in', 'T01 first', 'approved')",
        (epic_id, title),
    )

    ticket_ids = []
    for i in range(1, num_tickets + 1):
        tid = generate_id()
        ticket_ids.append(tid)
        conn.execute(
            "INSERT INTO planned_tickets "
            "(id, epic_id, sequence, title, goal, scope, non_goals, "
            "references_, implementation_notes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                tid,
                epic_id,
                i,
                f"Ticket {i}",
                f"Implement feature {i}",
                json.dumps([f"scope item {i}"]),
                json.dumps([f"non-goal {i}"]),
                json.dumps([f"docs/ref{i}.md"]),
                json.dumps([f"note {i}"]),
            ),
        )
        # Add one criterion per ticket
        conn.execute(
            "INSERT INTO planned_ticket_criteria (id, planned_ticket_id, description) "
            "VALUES (?, ?, ?)",
            (generate_id(), tid, f"Criterion for ticket {i}"),
        )

    # Add dependency: ticket 2 depends on ticket 1 (if we have 2+)
    if num_tickets >= 2:
        conn.execute(
            "INSERT INTO planned_ticket_dependencies "
            "(planned_ticket_id, depends_on_id) VALUES (?, ?)",
            (ticket_ids[1], ticket_ids[0]),
        )

    conn.commit()
    return epic_id, ticket_ids


# ---------------------------------------------------------------------------
# _slugify
# ---------------------------------------------------------------------------


class TestSlugify:
    def test_basic(self):
        assert _slugify("Build Auth System") == "build-auth-system"

    def test_special_chars(self):
        assert _slugify("Epic #1: Auth & Login!") == "epic-1-auth-login"

    def test_empty(self):
        assert _slugify("") == "untitled"

    def test_whitespace(self):
        assert _slugify("  lots   of   spaces  ") == "lots-of-spaces"


# ---------------------------------------------------------------------------
# materialize_epic — doc generation
# ---------------------------------------------------------------------------


class TestMaterializeEpicDocs:
    def test_creates_readme(self, conn, tmp_path):
        epic_id, _ = _create_approved_epic(conn)
        result = materialize_epic(conn, "p1", epic_id, tmp_path)

        readme = (
            tmp_path
            / "docs"
            / "tickets"
            / "generated"
            / "build-auth-system"
            / "README.md"
        )
        assert readme.exists()
        content = readme.read_text()
        assert "Build Auth System" in content
        assert "Add authentication" in content
        assert "T01" in content
        assert "T02" in content

    def test_creates_ticket_docs(self, conn, tmp_path):
        epic_id, _ = _create_approved_epic(conn)
        materialize_epic(conn, "p1", epic_id, tmp_path)

        gen_dir = tmp_path / "docs" / "tickets" / "generated" / "build-auth-system"
        t01 = gen_dir / "T01.md"
        t02 = gen_dir / "T02.md"
        assert t01.exists()
        assert t02.exists()

        t01_content = t01.read_text()
        assert "# T01: Ticket 1" in t01_content
        assert "## Goal" in t01_content
        assert "Implement feature 1" in t01_content
        assert "scope item 1" in t01_content
        assert "non-goal 1" in t01_content
        assert "Criterion for ticket 1" in t01_content
        assert "docs/ref1.md" in t01_content
        assert "note 1" in t01_content

        t02_content = t02.read_text()
        assert "## Dependencies" in t02_content
        assert "T01" in t02_content

    def test_result_counts(self, conn, tmp_path):
        epic_id, _ = _create_approved_epic(conn)
        result = materialize_epic(conn, "p1", epic_id, tmp_path)

        assert result.epic_id == epic_id
        assert result.docs_written == 3  # README + T01 + T02
        assert result.tickets_created == 2
        assert result.conflicts == []

    def test_sets_materialized_path(self, conn, tmp_path):
        epic_id, _ = _create_approved_epic(conn)
        materialize_epic(conn, "p1", epic_id, tmp_path)

        row = conn.execute(
            "SELECT materialized_path FROM planned_epics WHERE id = ?",
            (epic_id,),
        ).fetchone()
        assert row["materialized_path"] == "docs/tickets/generated/build-auth-system"


# ---------------------------------------------------------------------------
# materialize_epic — DB records
# ---------------------------------------------------------------------------


class TestMaterializeEpicDB:
    def test_creates_impl_tickets(self, conn, tmp_path):
        epic_id, planned_ids = _create_approved_epic(conn)
        materialize_epic(conn, "p1", epic_id, tmp_path)

        rows = conn.execute(
            "SELECT id, title, status, planned_ticket_id FROM tickets "
            "WHERE project_id = 'p1' ORDER BY title",
        ).fetchall()
        assert len(rows) == 2
        assert rows[0]["title"] == "Ticket 1"
        assert rows[0]["status"] == "ready"
        assert rows[0]["planned_ticket_id"] == planned_ids[0]
        assert rows[1]["planned_ticket_id"] == planned_ids[1]

    def test_creates_acceptance_criteria(self, conn, tmp_path):
        epic_id, _ = _create_approved_epic(conn)
        materialize_epic(conn, "p1", epic_id, tmp_path)

        impl_tickets = conn.execute(
            "SELECT id FROM tickets WHERE project_id = 'p1' ORDER BY title",
        ).fetchall()

        for ticket in impl_tickets:
            criteria = conn.execute(
                "SELECT description, status FROM acceptance_criteria "
                "WHERE ticket_id = ?",
                (ticket["id"],),
            ).fetchall()
            assert len(criteria) == 1
            assert criteria[0]["status"] == "pending"

    def test_creates_dependencies(self, conn, tmp_path):
        epic_id, _ = _create_approved_epic(conn)
        materialize_epic(conn, "p1", epic_id, tmp_path)

        impl_tickets = conn.execute(
            "SELECT id, title FROM tickets WHERE project_id = 'p1' ORDER BY title",
        ).fetchall()
        t1_id = impl_tickets[0]["id"]
        t2_id = impl_tickets[1]["id"]

        deps = conn.execute(
            "SELECT depends_on_id FROM ticket_dependencies WHERE ticket_id = ?",
            (t2_id,),
        ).fetchall()
        assert len(deps) == 1
        assert deps[0]["depends_on_id"] == t1_id

    def test_records_state_transitions(self, conn, tmp_path):
        epic_id, _ = _create_approved_epic(conn)
        materialize_epic(conn, "p1", epic_id, tmp_path)

        impl_tickets = conn.execute(
            "SELECT id FROM tickets WHERE project_id = 'p1'",
        ).fetchall()
        for ticket in impl_tickets:
            trans = conn.execute(
                "SELECT from_status, to_status, triggered_by, reason "
                "FROM state_transitions WHERE ticket_id = ?",
                (ticket["id"],),
            ).fetchone()
            assert trans["from_status"] == "null"
            assert trans["to_status"] == "ready"
            assert trans["triggered_by"] == "system"
            assert "materialized" in trans["reason"]

    def test_idempotent_tickets(self, conn, tmp_path):
        """Re-materialization does not duplicate implementation tickets."""
        epic_id, _ = _create_approved_epic(conn)
        materialize_epic(conn, "p1", epic_id, tmp_path, force=True)
        result = materialize_epic(conn, "p1", epic_id, tmp_path, force=True)

        assert result.tickets_created == 0

        count = conn.execute(
            "SELECT COUNT(*) as cnt FROM tickets WHERE project_id = 'p1'",
        ).fetchone()["cnt"]
        assert count == 2


# ---------------------------------------------------------------------------
# Hash-gating / regeneration
# ---------------------------------------------------------------------------


class TestHashGating:
    def test_stores_hashes(self, conn, tmp_path):
        epic_id, _ = _create_approved_epic(conn)
        materialize_epic(conn, "p1", epic_id, tmp_path)

        hashes = conn.execute(
            "SELECT file_path, content_hash FROM materialization_hashes "
            "WHERE epic_id = ?",
            (epic_id,),
        ).fetchall()
        # README + 2 tickets = 3 hashes
        assert len(hashes) == 3

    def test_overwrite_unedited(self, conn, tmp_path):
        """Re-materialization overwrites unedited files."""
        epic_id, _ = _create_approved_epic(conn)
        materialize_epic(conn, "p1", epic_id, tmp_path)

        gen_dir = tmp_path / "docs" / "tickets" / "generated" / "build-auth-system"
        t01_before = (gen_dir / "T01.md").read_text()

        # Re-materialize — should succeed with no conflicts
        result = materialize_epic(conn, "p1", epic_id, tmp_path)
        assert result.conflicts == []

        t01_after = (gen_dir / "T01.md").read_text()
        assert t01_before == t01_after

    def test_conflict_on_manual_edit(self, conn, tmp_path):
        """Edited files are reported as conflicts."""
        epic_id, _ = _create_approved_epic(conn)
        materialize_epic(conn, "p1", epic_id, tmp_path)

        gen_dir = tmp_path / "docs" / "tickets" / "generated" / "build-auth-system"
        t01_path = gen_dir / "T01.md"
        t01_path.write_text("manually edited content")

        result = materialize_epic(conn, "p1", epic_id, tmp_path)
        assert len(result.conflicts) == 1
        assert "T01.md" in result.conflicts[0].file_path

        # File should NOT have been overwritten
        assert t01_path.read_text() == "manually edited content"

    def test_force_overwrites_edited(self, conn, tmp_path):
        """--force overwrites manually edited files."""
        epic_id, _ = _create_approved_epic(conn)
        materialize_epic(conn, "p1", epic_id, tmp_path)

        gen_dir = tmp_path / "docs" / "tickets" / "generated" / "build-auth-system"
        t01_path = gen_dir / "T01.md"
        t01_path.write_text("manually edited content")

        result = materialize_epic(conn, "p1", epic_id, tmp_path, force=True)
        assert result.conflicts == []

        # File should have been overwritten
        assert t01_path.read_text() != "manually edited content"
        assert "# T01: Ticket 1" in t01_path.read_text()

    def test_force_updates_hash(self, conn, tmp_path):
        """--force updates the stored hash after overwriting."""
        epic_id, _ = _create_approved_epic(conn)
        materialize_epic(conn, "p1", epic_id, tmp_path)

        gen_dir = tmp_path / "docs" / "tickets" / "generated" / "build-auth-system"
        t01_path = gen_dir / "T01.md"
        t01_path.write_text("manually edited content")

        materialize_epic(conn, "p1", epic_id, tmp_path, force=True)

        # Hash should match the newly written content
        import hashlib

        new_content = t01_path.read_text()
        expected_hash = hashlib.sha256(new_content.encode()).hexdigest()

        row = conn.execute(
            "SELECT content_hash FROM materialization_hashes "
            "WHERE epic_id = ? AND file_path LIKE '%T01.md'",
            (epic_id,),
        ).fetchone()
        assert row["content_hash"] == expected_hash

    def test_new_file_written(self, conn, tmp_path):
        """Files that don't exist yet are always written."""
        epic_id, _ = _create_approved_epic(conn)
        result = materialize_epic(conn, "p1", epic_id, tmp_path)
        assert result.docs_written == 3
        assert result.conflicts == []


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class TestMaterializeValidation:
    def test_rejects_non_approved(self, conn, tmp_path):
        r = new_epic(conn, "p1", "problem")
        with pytest.raises(ValueError, match="expected 'approved'"):
            materialize_epic(conn, "p1", r.epic_id, tmp_path)

    def test_rejects_no_title(self, conn, tmp_path):
        from capsaicin.queries import generate_id

        epic_id = generate_id()
        conn.execute(
            "INSERT INTO planned_epics "
            "(id, project_id, problem_statement, status) "
            "VALUES (?, 'p1', 'problem', 'approved')",
            (epic_id,),
        )
        conn.commit()

        with pytest.raises(ValueError, match="no title"):
            materialize_epic(conn, "p1", epic_id, tmp_path)

    def test_rejects_no_tickets(self, conn, tmp_path):
        from capsaicin.queries import generate_id

        epic_id = generate_id()
        conn.execute(
            "INSERT INTO planned_epics "
            "(id, project_id, problem_statement, title, status) "
            "VALUES (?, 'p1', 'problem', 'Some Title', 'approved')",
            (epic_id,),
        )
        conn.commit()

        with pytest.raises(ValueError, match="no planned tickets"):
            materialize_epic(conn, "p1", epic_id, tmp_path)


# ---------------------------------------------------------------------------
# Approve triggers materialization
# ---------------------------------------------------------------------------


class TestApproveWithMaterialization:
    def test_approve_materializes(self, conn, tmp_path):
        """plan approve creates docs and impl tickets when repo_root provided."""
        epic_id, planned_ids = _create_approved_epic(conn)
        # Reset to human-gate for approval flow
        conn.execute(
            "UPDATE planned_epics SET status = 'human-gate', "
            "gate_reason = 'review_passed' WHERE id = ?",
            (epic_id,),
        )
        conn.commit()

        result = approve(
            conn,
            "p1",
            epic_id=epic_id,
            rationale="lgtm",
            repo_root=tmp_path,
        )

        assert result.final_status == "approved"
        assert "materialized" in result.detail

        # Docs created
        gen_dir = tmp_path / "docs" / "tickets" / "generated" / "build-auth-system"
        assert (gen_dir / "README.md").exists()
        assert (gen_dir / "T01.md").exists()

        # Impl tickets created
        count = conn.execute(
            "SELECT COUNT(*) as cnt FROM tickets WHERE project_id = 'p1'",
        ).fetchone()["cnt"]
        assert count == 2

    def test_approve_without_repo_root(self, conn):
        """plan approve without repo_root skips materialization."""
        epic_id, _ = _create_approved_epic(conn)
        conn.execute(
            "UPDATE planned_epics SET status = 'human-gate', "
            "gate_reason = 'review_passed' WHERE id = ?",
            (epic_id,),
        )
        conn.commit()

        result = approve(conn, "p1", epic_id=epic_id)
        assert result.final_status == "approved"

        # No impl tickets created
        count = conn.execute(
            "SELECT COUNT(*) as cnt FROM tickets WHERE project_id = 'p1'",
        ).fetchone()["cnt"]
        assert count == 0


# ---------------------------------------------------------------------------
# Activity logging
# ---------------------------------------------------------------------------


class TestMaterializeLogging:
    def test_logs_materialization(self, conn, tmp_path):
        epic_id, _ = _create_approved_epic(conn)
        log = tmp_path / "activity.log"
        materialize_epic(conn, "p1", epic_id, tmp_path, log_path=log)

        content = log.read_text()
        assert "EPIC_MATERIALIZED" in content
