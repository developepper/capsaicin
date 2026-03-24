"""Tests for backend evidence data model, dataclasses, and CRUD helpers."""

from __future__ import annotations

import sqlite3

import pytest

from capsaicin.adapters.types import BackendEvidence, EvidenceRequirement
from capsaicin.db import get_connection, run_migrations
from capsaicin.queries import (
    clear_evidence_from_requirements,
    delete_backend_evidence,
    fulfill_evidence_requirement,
    generate_id,
    insert_backend_evidence,
    insert_evidence_requirement,
    load_backend_evidence_by_id,
    load_backend_evidence_for_epic,
    load_backend_evidence_for_ticket,
    load_evidence_requirement_by_id,
    load_evidence_requirements_for_epic,
    load_evidence_requirements_for_ticket,
    waive_evidence_requirement,
)


@pytest.fixture
def conn():
    c = get_connection(":memory:")
    run_migrations(c)
    c.execute(
        "INSERT INTO projects (id, name, repo_path) VALUES (?, ?, ?)",
        ("p1", "test", "/tmp/repo"),
    )
    c.execute(
        "INSERT INTO planned_epics (id, project_id, problem_statement, status) "
        "VALUES (?, ?, ?, ?)",
        ("e1", "p1", "problem", "new"),
    )
    c.execute(
        "INSERT INTO planned_tickets "
        "(id, epic_id, sequence, title, goal, scope, non_goals, references_, implementation_notes) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("pt1", "e1", 1, "Ticket 1", "goal", "[]", "[]", "[]", "[]"),
    )
    c.commit()
    yield c
    c.close()


# ---------------------------------------------------------------------------
# Dataclass round-trip tests
# ---------------------------------------------------------------------------


class TestBackendEvidenceDataclass:
    def test_to_dict_from_dict_round_trip(self):
        evidence = BackendEvidence(
            id="ev1",
            epic_id="e1",
            evidence_type="command_output",
            title="Test ls output",
            planned_ticket_id="pt1",
            body="Listed directory contents",
            command="ls -la /tmp",
            stdout="total 0\ndrwxr-xr-x  2 user user 40 Jan 1 00:00 .",
            stderr="",
            structured_data={"exit_code": 0, "files": [".", ".."]},
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
        )
        d = evidence.to_dict()
        restored = BackendEvidence.from_dict(d)
        assert restored.to_dict() == d

    def test_from_dict_with_json_string_structured_data(self):
        data = {
            "id": "ev1",
            "epic_id": "e1",
            "evidence_type": "structured_result",
            "title": "API response",
            "structured_data": '{"status": 200}',
        }
        evidence = BackendEvidence.from_dict(data)
        assert evidence.structured_data == {"status": 200}

    def test_minimal_fields(self):
        evidence = BackendEvidence(
            id="ev1",
            epic_id="e1",
            evidence_type="behavioral_note",
            title="Observed behavior",
        )
        d = evidence.to_dict()
        restored = BackendEvidence.from_dict(d)
        assert restored.id == "ev1"
        assert restored.body is None
        assert restored.command is None
        assert restored.structured_data is None

    def test_invalid_evidence_type_raises(self):
        with pytest.raises(ValueError, match="evidence_type"):
            BackendEvidence(
                id="ev1",
                epic_id="e1",
                evidence_type="invalid",
                title="Bad",
            )

    def test_all_evidence_types_valid(self):
        for etype in (
            "command",
            "output_envelope",
            "structured_result_sample",
            "command_output",
            "structured_result",
            "permission_denial",
            "behavioral_note",
        ):
            ev = BackendEvidence(id="x", epic_id="e1", evidence_type=etype, title="t")
            assert ev.evidence_type == etype


class TestEvidenceRequirementDataclass:
    def test_to_dict_from_dict_round_trip(self):
        req = EvidenceRequirement(
            id="req1",
            epic_id="e1",
            description="Need to verify API endpoint returns 200",
            planned_ticket_id="pt1",
            suggested_command="curl -s -o /dev/null -w '%{http_code}' http://localhost:8000/api/health",
            status="pending",
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
        )
        d = req.to_dict()
        restored = EvidenceRequirement.from_dict(d)
        assert restored.to_dict() == d

    def test_suggested_command_field_present(self):
        req = EvidenceRequirement(
            id="req1",
            epic_id="e1",
            description="Check permissions",
            suggested_command="ls -la /etc/shadow",
        )
        assert req.suggested_command == "ls -la /etc/shadow"
        d = req.to_dict()
        assert d["suggested_command"] == "ls -la /etc/shadow"

    def test_minimal_fields(self):
        req = EvidenceRequirement(
            id="req1",
            epic_id="e1",
            description="Some requirement",
        )
        assert req.suggested_command is None
        assert req.status == "pending"
        assert req.fulfilled_by is None

    def test_fulfilled_status(self):
        req = EvidenceRequirement(
            id="req1",
            epic_id="e1",
            description="Fulfilled requirement",
            status="fulfilled",
            fulfilled_by="ev1",
        )
        assert req.status == "fulfilled"
        assert req.fulfilled_by == "ev1"

    def test_invalid_status_raises(self):
        with pytest.raises(ValueError, match="status"):
            EvidenceRequirement(
                id="req1",
                epic_id="e1",
                description="Bad",
                status="invalid",
            )


# ---------------------------------------------------------------------------
# Migration / schema tests
# ---------------------------------------------------------------------------


class TestMigrationSchema:
    def test_backend_evidence_table_exists(self, conn):
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='backend_evidence'"
        ).fetchone()
        assert row is not None

    def test_evidence_requirements_table_exists(self, conn):
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='evidence_requirements'"
        ).fetchone()
        assert row is not None

    def test_backend_evidence_fk_to_epic(self, conn):
        """Evidence must reference an existing epic."""
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO backend_evidence "
                "(id, epic_id, evidence_type, title) "
                "VALUES (?, ?, ?, ?)",
                ("ev_bad", "nonexistent", "command_output", "Bad"),
            )

    def test_evidence_requirements_fk_to_epic(self, conn):
        """Requirements must reference an existing epic."""
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO evidence_requirements "
                "(id, epic_id, description) "
                "VALUES (?, ?, ?)",
                ("req_bad", "nonexistent", "Bad"),
            )

    def test_evidence_type_check_constraint(self, conn):
        """Only valid evidence types are allowed."""
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO backend_evidence "
                "(id, epic_id, evidence_type, title) "
                "VALUES (?, ?, ?, ?)",
                ("ev_bad", "e1", "invalid_type", "Bad"),
            )

    def test_requirement_status_check_constraint(self, conn):
        """Only valid statuses are allowed."""
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO evidence_requirements "
                "(id, epic_id, description, status) "
                "VALUES (?, ?, ?, ?)",
                ("req_bad", "e1", "Bad", "invalid_status"),
            )


# ---------------------------------------------------------------------------
# CRUD helper tests
# ---------------------------------------------------------------------------


class TestInsertAndLoadEvidence:
    def test_insert_and_load_for_epic(self, conn):
        evidence = BackendEvidence(
            id=generate_id(),
            epic_id="e1",
            evidence_type="command_output",
            title="ls output",
            command="ls -la",
            stdout="file1\nfile2",
            stderr="",
        )
        insert_backend_evidence(conn, evidence)
        results = load_backend_evidence_for_epic(conn, "e1")
        assert len(results) == 1
        assert results[0].title == "ls output"
        assert results[0].command == "ls -la"
        assert results[0].stdout == "file1\nfile2"

    def test_insert_and_load_for_ticket(self, conn):
        evidence = BackendEvidence(
            id=generate_id(),
            epic_id="e1",
            planned_ticket_id="pt1",
            evidence_type="permission_denial",
            title="Permission denied on /etc/shadow",
            body="Operation not permitted",
        )
        insert_backend_evidence(conn, evidence)
        results = load_backend_evidence_for_ticket(conn, "pt1")
        assert len(results) == 1
        assert results[0].evidence_type == "permission_denial"
        assert results[0].planned_ticket_id == "pt1"

    def test_structured_data_round_trip(self, conn):
        evidence = BackendEvidence(
            id=generate_id(),
            epic_id="e1",
            evidence_type="structured_result",
            title="API response sample",
            structured_data={"status": 200, "body": {"ok": True}},
        )
        insert_backend_evidence(conn, evidence)
        results = load_backend_evidence_for_epic(conn, "e1")
        assert results[0].structured_data == {"status": 200, "body": {"ok": True}}

    def test_load_empty_results(self, conn):
        results = load_backend_evidence_for_epic(conn, "e1")
        assert results == []

    def test_multiple_evidence_items(self, conn):
        for i in range(3):
            insert_backend_evidence(
                conn,
                BackendEvidence(
                    id=generate_id(),
                    epic_id="e1",
                    evidence_type="behavioral_note",
                    title=f"Note {i}",
                ),
            )
        results = load_backend_evidence_for_epic(conn, "e1")
        assert len(results) == 3


class TestInsertAndLoadRequirements:
    def test_insert_and_load_for_epic(self, conn):
        req = EvidenceRequirement(
            id=generate_id(),
            epic_id="e1",
            description="Verify API health",
            suggested_command="curl http://localhost:8000/health",
        )
        insert_evidence_requirement(conn, req)
        results = load_evidence_requirements_for_epic(conn, "e1")
        assert len(results) == 1
        assert results[0].description == "Verify API health"
        assert results[0].suggested_command == "curl http://localhost:8000/health"

    def test_insert_and_load_for_ticket(self, conn):
        req = EvidenceRequirement(
            id=generate_id(),
            epic_id="e1",
            planned_ticket_id="pt1",
            description="Check file permissions",
            suggested_command="stat /tmp/testfile",
        )
        insert_evidence_requirement(conn, req)
        results = load_evidence_requirements_for_ticket(conn, "pt1")
        assert len(results) == 1
        assert results[0].planned_ticket_id == "pt1"

    def test_load_empty_results(self, conn):
        results = load_evidence_requirements_for_epic(conn, "e1")
        assert results == []

    def test_fulfilled_requirement_with_evidence_link(self, conn):
        ev_id = generate_id()
        insert_backend_evidence(
            conn,
            BackendEvidence(
                id=ev_id,
                epic_id="e1",
                evidence_type="command_output",
                title="Health check output",
                command="curl http://localhost:8000/health",
                stdout='{"status": "ok"}',
            ),
        )
        req = EvidenceRequirement(
            id=generate_id(),
            epic_id="e1",
            description="Verify API health",
            suggested_command="curl http://localhost:8000/health",
            status="fulfilled",
            fulfilled_by=ev_id,
        )
        insert_evidence_requirement(conn, req)
        results = load_evidence_requirements_for_epic(conn, "e1")
        assert results[0].status == "fulfilled"
        assert results[0].fulfilled_by == ev_id


# ---------------------------------------------------------------------------
# By-ID lookup tests
# ---------------------------------------------------------------------------


class TestLoadByIdQueries:
    def test_load_evidence_by_id(self, conn):
        ev_id = generate_id()
        insert_backend_evidence(
            conn,
            BackendEvidence(
                id=ev_id,
                epic_id="e1",
                evidence_type="command_output",
                title="Test evidence",
                command="echo hello",
                stdout="hello",
            ),
        )
        result = load_backend_evidence_by_id(conn, ev_id)
        assert result is not None
        assert result.id == ev_id
        assert result.title == "Test evidence"
        assert result.stdout == "hello"

    def test_load_evidence_by_id_not_found(self, conn):
        result = load_backend_evidence_by_id(conn, "nonexistent")
        assert result is None

    def test_load_requirement_by_id(self, conn):
        req_id = generate_id()
        insert_evidence_requirement(
            conn,
            EvidenceRequirement(
                id=req_id,
                epic_id="e1",
                description="Check something",
                suggested_command="echo test",
            ),
        )
        result = load_evidence_requirement_by_id(conn, req_id)
        assert result is not None
        assert result.id == req_id
        assert result.description == "Check something"
        assert result.status == "pending"

    def test_load_requirement_by_id_not_found(self, conn):
        result = load_evidence_requirement_by_id(conn, "nonexistent")
        assert result is None


# ---------------------------------------------------------------------------
# Fulfill and waive workflow tests
# ---------------------------------------------------------------------------


class TestFulfillAndWaiveWorkflow:
    def test_fulfill_requirement(self, conn):
        ev_id = generate_id()
        insert_backend_evidence(
            conn,
            BackendEvidence(
                id=ev_id,
                epic_id="e1",
                evidence_type="command_output",
                title="Output",
            ),
        )
        req_id = generate_id()
        insert_evidence_requirement(
            conn,
            EvidenceRequirement(id=req_id, epic_id="e1", description="Need output"),
        )

        req_before = load_evidence_requirement_by_id(conn, req_id)
        assert req_before.status == "pending"
        assert req_before.fulfilled_by is None

        fulfill_evidence_requirement(conn, req_id, ev_id)

        req_after = load_evidence_requirement_by_id(conn, req_id)
        assert req_after.status == "fulfilled"
        assert req_after.fulfilled_by == ev_id

    def test_waive_requirement(self, conn):
        req_id = generate_id()
        insert_evidence_requirement(
            conn,
            EvidenceRequirement(id=req_id, epic_id="e1", description="Optional check"),
        )

        waive_evidence_requirement(conn, req_id)

        req = load_evidence_requirement_by_id(conn, req_id)
        assert req.status == "waived"

    def test_waived_status_in_dataclass(self):
        req = EvidenceRequirement(
            id="req1",
            epic_id="e1",
            description="Waived requirement",
            status="waived",
        )
        assert req.status == "waived"
        d = req.to_dict()
        restored = EvidenceRequirement.from_dict(d)
        assert restored.status == "waived"

    def test_waived_status_in_db_constraint(self, conn):
        """Waived status is accepted by the DB CHECK constraint."""
        conn.execute(
            "INSERT INTO evidence_requirements (id, epic_id, description, status) "
            "VALUES (?, ?, ?, ?)",
            ("req_waived", "e1", "waived test", "waived"),
        )
        row = conn.execute(
            "SELECT status FROM evidence_requirements WHERE id = ?", ("req_waived",)
        ).fetchone()
        assert row["status"] == "waived"


# ---------------------------------------------------------------------------
# Expanded evidence type tests
# ---------------------------------------------------------------------------


class TestExpandedEvidenceTypes:
    def test_new_types_accepted_by_db(self, conn):
        """The three new ticket-spec types are accepted by the CHECK constraint."""
        for etype in ("command", "output_envelope", "structured_result_sample"):
            eid = generate_id()
            insert_backend_evidence(
                conn,
                BackendEvidence(
                    id=eid, epic_id="e1", evidence_type=etype, title=f"test {etype}"
                ),
            )
            result = load_backend_evidence_by_id(conn, eid)
            assert result is not None
            assert result.evidence_type == etype

    def test_output_envelope_stores_stdout_stderr(self, conn):
        eid = generate_id()
        insert_backend_evidence(
            conn,
            BackendEvidence(
                id=eid,
                epic_id="e1",
                evidence_type="output_envelope",
                title="CLI capture",
                stdout="OK",
                stderr="warn: deprecated",
                structured_data={"exit_code": 0},
            ),
        )
        result = load_backend_evidence_by_id(conn, eid)
        assert result.stdout == "OK"
        assert result.stderr == "warn: deprecated"
        assert result.structured_data == {"exit_code": 0}


# ---------------------------------------------------------------------------
# Delete evidence with fulfilled requirement tests
# ---------------------------------------------------------------------------


class TestDeleteEvidenceWithRequirement:
    def test_clear_evidence_resets_requirement_to_pending(self, conn):
        ev_id = generate_id()
        insert_backend_evidence(
            conn,
            BackendEvidence(
                id=ev_id, epic_id="e1", evidence_type="command_output", title="Output"
            ),
        )
        req_id = generate_id()
        insert_evidence_requirement(
            conn,
            EvidenceRequirement(id=req_id, epic_id="e1", description="Need output"),
        )
        fulfill_evidence_requirement(conn, req_id, ev_id)

        req = load_evidence_requirement_by_id(conn, req_id)
        assert req.status == "fulfilled"
        assert req.fulfilled_by == ev_id

        clear_evidence_from_requirements(conn, ev_id)

        req = load_evidence_requirement_by_id(conn, req_id)
        assert req.status == "pending"
        assert req.fulfilled_by is None

    def test_delete_fulfilled_evidence_does_not_raise(self, conn):
        """Deleting evidence that fulfills a requirement should not cause FK error."""
        ev_id = generate_id()
        insert_backend_evidence(
            conn,
            BackendEvidence(
                id=ev_id, epic_id="e1", evidence_type="command_output", title="Output"
            ),
        )
        req_id = generate_id()
        insert_evidence_requirement(
            conn,
            EvidenceRequirement(id=req_id, epic_id="e1", description="Need output"),
        )
        fulfill_evidence_requirement(conn, req_id, ev_id)

        # This mirrors the action_delete_evidence flow
        clear_evidence_from_requirements(conn, ev_id)
        delete_backend_evidence(conn, ev_id)

        assert load_backend_evidence_by_id(conn, ev_id) is None
        req = load_evidence_requirement_by_id(conn, req_id)
        assert req.status == "pending"
        assert req.fulfilled_by is None
