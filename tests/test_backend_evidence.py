"""Tests for backend evidence data model, dataclasses, and CRUD helpers."""

from __future__ import annotations

import sqlite3

import pytest

from capsaicin.adapters.types import BackendEvidence, EvidenceRequirement
from capsaicin.db import get_connection, run_migrations
from capsaicin.queries import (
    generate_id,
    insert_backend_evidence,
    insert_evidence_requirement,
    load_backend_evidence_for_epic,
    load_backend_evidence_for_ticket,
    load_evidence_requirements_for_epic,
    load_evidence_requirements_for_ticket,
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
