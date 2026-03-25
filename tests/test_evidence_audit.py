"""Tests for evidence audit trail and inspectability (T09)."""

from __future__ import annotations

import pytest

from capsaicin.adapters.types import BackendEvidence, EvidenceRequirement
from capsaicin.db import get_connection, run_migrations
from capsaicin.queries import (
    delete_backend_evidence,
    fulfill_evidence_requirement,
    generate_id,
    insert_backend_evidence,
    insert_evidence_requirement,
    load_evidence_for_run,
    load_evidence_timeline,
    load_runs_for_evidence,
    record_run_evidence,
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
        "(id, epic_id, sequence, title, goal, scope, non_goals, "
        "references_, implementation_notes) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("pt1", "e1", 1, "Ticket 1", "goal", "[]", "[]", "[]", "[]"),
    )
    c.commit()
    yield c
    c.close()


def _insert_evidence(conn, evidence_id="ev1", title="Test evidence"):
    ev = BackendEvidence(
        id=evidence_id,
        epic_id="e1",
        evidence_type="command",
        title=title,
        command="echo hello",
    )
    insert_backend_evidence(conn, ev)
    conn.commit()
    return evidence_id


def _insert_planning_run(conn, run_id, epic_id="e1"):
    """Insert a minimal agent_runs row for a planning run."""
    conn.execute(
        "INSERT INTO agent_runs "
        "(id, epic_id, role, mode, cycle_number, attempt_number, "
        "exit_status, prompt, run_request, started_at) "
        "VALUES (?, ?, 'planner', 'read-write', 1, 1, 'success', 'p', '{}', "
        "datetime('now'))",
        (run_id, epic_id),
    )
    conn.commit()


def _insert_impl_run(conn, run_id, ticket_id):
    """Insert a minimal agent_runs row for an implementation run."""
    conn.execute(
        "INSERT INTO agent_runs "
        "(id, ticket_id, role, mode, cycle_number, attempt_number, "
        "exit_status, prompt, run_request, started_at) "
        "VALUES (?, ?, 'implementer', 'read-write', 1, 1, 'success', 'p', '{}', "
        "datetime('now'))",
        (run_id, ticket_id),
    )
    conn.commit()


def _insert_impl_ticket(conn, ticket_id, planned_ticket_id="pt1"):
    """Insert a minimal implementation ticket linked to a planned ticket."""
    conn.execute(
        "INSERT INTO tickets "
        "(id, project_id, title, description, status, planned_ticket_id) "
        "VALUES (?, 'p1', 'Impl ticket', 'desc', 'ready', ?)",
        (ticket_id, planned_ticket_id),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# record_run_evidence / load_evidence_for_run / load_runs_for_evidence
# ---------------------------------------------------------------------------


class TestRunEvidenceRecording:
    def test_record_and_load_evidence_for_run(self, conn):
        ev_id = _insert_evidence(conn)
        run_id = generate_id()
        _insert_planning_run(conn, run_id)

        record_run_evidence(conn, run_id, [ev_id])
        conn.commit()

        result = load_evidence_for_run(conn, run_id)
        assert len(result) == 1
        assert result[0]["id"] == ev_id
        assert result[0]["title"] == "Test evidence"
        assert result[0]["evidence_type"] == "command"

    def test_load_runs_for_evidence(self, conn):
        ev_id = _insert_evidence(conn)
        run_id = generate_id()
        _insert_planning_run(conn, run_id)

        record_run_evidence(conn, run_id, [ev_id])
        conn.commit()

        result = load_runs_for_evidence(conn, ev_id)
        assert len(result) == 1
        assert result[0]["id"] == run_id
        assert result[0]["role"] == "planner"

    def test_duplicate_insert_ignored(self, conn):
        ev_id = _insert_evidence(conn)
        run_id = generate_id()
        _insert_planning_run(conn, run_id)

        record_run_evidence(conn, run_id, [ev_id])
        record_run_evidence(conn, run_id, [ev_id])
        conn.commit()

        result = load_evidence_for_run(conn, run_id)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# Evidence timeline
# ---------------------------------------------------------------------------


class TestEvidenceTimeline:
    def test_timeline_includes_evidence_added(self, conn):
        _insert_evidence(conn)

        timeline = load_evidence_timeline(conn, "e1")
        added = [e for e in timeline if e["event_type"] == "evidence_added"]
        assert len(added) == 1
        assert added[0]["title"] == "Test evidence"

    def test_timeline_includes_requirement_events(self, conn):
        req_id = generate_id()
        insert_evidence_requirement(
            conn,
            EvidenceRequirement(id=req_id, epic_id="e1", description="Need output"),
        )
        conn.commit()

        timeline = load_evidence_timeline(conn, "e1")
        created = [e for e in timeline if e["event_type"] == "requirement_created"]
        assert len(created) == 1

        waive_evidence_requirement(conn, req_id)
        conn.commit()

        timeline2 = load_evidence_timeline(conn, "e1")
        waived = [e for e in timeline2 if e["event_type"] == "requirement_waived"]
        assert len(waived) == 1

    def test_timeline_includes_planning_run_consumption(self, conn):
        ev_id = _insert_evidence(conn)
        run_id = generate_id()
        _insert_planning_run(conn, run_id)
        record_run_evidence(conn, run_id, [ev_id])
        conn.commit()

        timeline = load_evidence_timeline(conn, "e1")
        consumed = [e for e in timeline if e["event_type"] == "evidence_consumed"]
        assert len(consumed) == 1
        assert consumed[0]["run_id"] == run_id

    def test_timeline_includes_implementation_run_consumption(self, conn):
        """Implementation runs (ticket_id, not epic_id) must appear
        on the epic timeline when they consume epic evidence."""
        ev_id = _insert_evidence(conn)
        ticket_id = generate_id()
        _insert_impl_ticket(conn, ticket_id)
        run_id = generate_id()
        _insert_impl_run(conn, run_id, ticket_id)
        record_run_evidence(conn, run_id, [ev_id])
        conn.commit()

        timeline = load_evidence_timeline(conn, "e1")
        consumed = [e for e in timeline if e["event_type"] == "evidence_consumed"]
        assert len(consumed) == 1
        assert consumed[0]["run_id"] == run_id
        assert consumed[0]["detail"] == "consumed by implementer run"

    def test_timeline_chronological_order(self, conn):
        # Create evidence, then a requirement, then a run that consumes it
        ev_id = _insert_evidence(conn)

        req_id = generate_id()
        insert_evidence_requirement(
            conn,
            EvidenceRequirement(id=req_id, epic_id="e1", description="Need it"),
        )
        conn.commit()

        run_id = generate_id()
        _insert_planning_run(conn, run_id)
        record_run_evidence(conn, run_id, [ev_id])
        conn.commit()

        timeline = load_evidence_timeline(conn, "e1")
        types = [e["event_type"] for e in timeline]
        assert "evidence_added" in types
        assert "requirement_created" in types
        assert "evidence_consumed" in types
        # All events should be in chronological order
        timestamps = [e["timestamp"] for e in timeline]
        assert timestamps == sorted(timestamps)


# ---------------------------------------------------------------------------
# Audit durability on evidence deletion
# ---------------------------------------------------------------------------


class TestAuditDurabilityOnDelete:
    def test_agent_run_evidence_preserved_after_delete(self, conn):
        """Deleting evidence must not remove agent_run_evidence audit rows,
        and load_evidence_for_run must still return the orphaned row."""
        ev_id = _insert_evidence(conn)
        run_id = generate_id()
        _insert_planning_run(conn, run_id)
        record_run_evidence(conn, run_id, [ev_id])
        conn.commit()

        # Verify the link exists with real data
        before = load_evidence_for_run(conn, run_id)
        assert len(before) == 1
        assert before[0]["title"] == "Test evidence"

        # Delete the evidence
        delete_backend_evidence(conn, ev_id)
        conn.commit()

        # The read-model query must still return the orphaned row
        after = load_evidence_for_run(conn, run_id)
        assert len(after) == 1
        assert after[0]["id"] == ev_id
        assert after[0]["title"] == "(deleted evidence)"
        assert after[0]["evidence_type"] == "unknown"

    def test_requirement_events_preserved_after_delete(self, conn):
        """Deleting evidence must not remove evidence_requirement_events
        audit rows."""
        ev_id = _insert_evidence(conn)
        req_id = generate_id()
        insert_evidence_requirement(
            conn,
            EvidenceRequirement(id=req_id, epic_id="e1", description="Need output"),
        )
        fulfill_evidence_requirement(conn, req_id, ev_id)
        conn.commit()

        # Verify events exist (created + satisfied)
        events_before = conn.execute(
            "SELECT id FROM evidence_requirement_events WHERE requirement_id = ?",
            (req_id,),
        ).fetchall()
        assert len(events_before) >= 2  # created + satisfied

        # Delete the evidence — this also resets the requirement to pending,
        # which adds a reset_to_pending event
        delete_backend_evidence(conn, ev_id)
        conn.commit()

        # All original events plus the reset event must be preserved
        events_after = conn.execute(
            "SELECT event_type FROM evidence_requirement_events "
            "WHERE requirement_id = ? ORDER BY created_at",
            (req_id,),
        ).fetchall()
        types = [e["event_type"] for e in events_after]
        assert "created" in types
        assert "satisfied" in types
        assert "reset_to_pending" in types
        assert len(events_after) >= len(events_before)

    def test_timeline_consumption_survives_evidence_delete(self, conn):
        """The timeline must still show consumption events after the
        evidence record has been deleted."""
        ev_id = _insert_evidence(conn)
        run_id = generate_id()
        _insert_planning_run(conn, run_id)
        record_run_evidence(conn, run_id, [ev_id])
        conn.commit()

        # Timeline shows consumption before delete
        tl_before = load_evidence_timeline(conn, "e1")
        consumed_before = [
            e for e in tl_before if e["event_type"] == "evidence_consumed"
        ]
        assert len(consumed_before) == 1

        # Delete evidence
        delete_backend_evidence(conn, ev_id)
        conn.commit()

        # Timeline must still show the consumption event
        tl_after = load_evidence_timeline(conn, "e1")
        consumed_after = [e for e in tl_after if e["event_type"] == "evidence_consumed"]
        assert len(consumed_after) == 1
        assert consumed_after[0]["title"] == "(deleted evidence)"
        assert consumed_after[0]["run_id"] == run_id

    def test_requirement_satisfy_event_records_evidence_id(self, conn):
        """The satisfy event must record which evidence_id was used."""
        ev_id = _insert_evidence(conn)
        req_id = generate_id()
        insert_evidence_requirement(
            conn,
            EvidenceRequirement(id=req_id, epic_id="e1", description="Need output"),
        )
        fulfill_evidence_requirement(conn, req_id, ev_id)
        conn.commit()

        row = conn.execute(
            "SELECT evidence_id FROM evidence_requirement_events "
            "WHERE requirement_id = ? AND event_type = 'satisfied'",
            (req_id,),
        ).fetchone()
        assert row is not None
        assert row["evidence_id"] == ev_id
