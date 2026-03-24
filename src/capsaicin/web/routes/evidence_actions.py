"""POST action routes for backend validation evidence and requirements.

Handles creating evidence records, creating/satisfying/waiving requirements,
and the paste-output clarification workflow.
"""

from __future__ import annotations

import json
import logging

from starlette.requests import Request
from starlette.responses import RedirectResponse

from capsaicin.adapters.types import BackendEvidence, EvidenceRequirement
from capsaicin.queries import (
    clear_evidence_from_requirements,
    delete_backend_evidence,
    fulfill_evidence_requirement,
    generate_id,
    insert_backend_evidence,
    insert_evidence_requirement,
    load_backend_evidence_by_id,
    load_evidence_requirement_by_id,
    waive_evidence_requirement,
)

_log = logging.getLogger(__name__)

VALID_EVIDENCE_TYPES = {
    "command",
    "output_envelope",
    "structured_result_sample",
    "command_output",
    "structured_result",
    "permission_denial",
    "behavioral_note",
}


def _error_redirect(request: Request, epic_id: str, message: str) -> RedirectResponse:
    url = request.url_for("epic_detail", epic_id=epic_id).include_query_params(
        error=message
    )
    return RedirectResponse(str(url), status_code=303)


async def action_create_evidence(request: Request) -> RedirectResponse:
    """POST /epics/{epic_id}/evidence — create a new evidence record."""
    conn = request.state.conn
    epic_id = request.path_params["epic_id"]
    form = await request.form()

    evidence_type = form.get("evidence_type", "").strip()
    title = form.get("title", "").strip()

    if not title:
        return _error_redirect(request, epic_id, "Evidence title is required.")
    if evidence_type not in VALID_EVIDENCE_TYPES:
        return _error_redirect(
            request, epic_id, f"Invalid evidence type: {evidence_type}"
        )

    body = form.get("body", "").strip() or None
    command = form.get("command", "").strip() or None
    stdout = form.get("stdout", "").strip() or None
    stderr = form.get("stderr", "").strip() or None

    structured_data_raw = form.get("structured_data", "").strip()
    structured_data = None
    if structured_data_raw:
        try:
            structured_data = json.loads(structured_data_raw)
        except json.JSONDecodeError:
            return _error_redirect(
                request, epic_id, "structured_data must be valid JSON."
            )

    evidence = BackendEvidence(
        id=generate_id(),
        epic_id=epic_id,
        evidence_type=evidence_type,
        title=title,
        body=body,
        command=command,
        stdout=stdout,
        stderr=stderr,
        structured_data=structured_data,
    )

    insert_backend_evidence(conn, evidence)
    conn.commit()

    return RedirectResponse(
        str(request.url_for("epic_detail", epic_id=epic_id)), status_code=303
    )


async def action_create_requirement(request: Request) -> RedirectResponse:
    """POST /epics/{epic_id}/requirements — create a new evidence requirement."""
    conn = request.state.conn
    epic_id = request.path_params["epic_id"]
    form = await request.form()

    description = form.get("description", "").strip()
    if not description:
        return _error_redirect(request, epic_id, "Requirement description is required.")

    suggested_command = form.get("suggested_command", "").strip() or None

    requirement = EvidenceRequirement(
        id=generate_id(),
        epic_id=epic_id,
        description=description,
        suggested_command=suggested_command,
    )

    insert_evidence_requirement(conn, requirement)
    conn.commit()

    return RedirectResponse(
        str(request.url_for("epic_detail", epic_id=epic_id)), status_code=303
    )


async def action_satisfy_requirement(request: Request) -> RedirectResponse:
    """POST /epics/{epic_id}/requirements/{req_id}/satisfy — link evidence to a requirement."""
    conn = request.state.conn
    epic_id = request.path_params["epic_id"]
    req_id = request.path_params["req_id"]
    form = await request.form()

    evidence_id = form.get("evidence_id", "").strip()
    if not evidence_id:
        return _error_redirect(
            request, epic_id, "Evidence ID is required to satisfy a requirement."
        )

    requirement = load_evidence_requirement_by_id(conn, req_id)
    if requirement is None:
        return _error_redirect(request, epic_id, "Requirement not found.")
    if requirement.epic_id != epic_id:
        return _error_redirect(
            request, epic_id, "Requirement does not belong to this epic."
        )
    if requirement.status != "pending":
        return _error_redirect(request, epic_id, "Requirement is not pending.")

    evidence = load_backend_evidence_by_id(conn, evidence_id)
    if evidence is None:
        return _error_redirect(request, epic_id, "Evidence record not found.")
    if evidence.epic_id != epic_id:
        return _error_redirect(
            request, epic_id, "Evidence does not belong to this epic."
        )

    fulfill_evidence_requirement(conn, req_id, evidence_id)
    conn.commit()

    return RedirectResponse(
        str(request.url_for("epic_detail", epic_id=epic_id)), status_code=303
    )


async def action_waive_requirement(request: Request) -> RedirectResponse:
    """POST /epics/{epic_id}/requirements/{req_id}/waive — waive a requirement."""
    conn = request.state.conn
    epic_id = request.path_params["epic_id"]
    req_id = request.path_params["req_id"]

    requirement = load_evidence_requirement_by_id(conn, req_id)
    if requirement is None:
        return _error_redirect(request, epic_id, "Requirement not found.")
    if requirement.epic_id != epic_id:
        return _error_redirect(
            request, epic_id, "Requirement does not belong to this epic."
        )
    if requirement.status != "pending":
        return _error_redirect(request, epic_id, "Requirement is not pending.")

    waive_evidence_requirement(conn, req_id)
    conn.commit()

    return RedirectResponse(
        str(request.url_for("epic_detail", epic_id=epic_id)), status_code=303
    )


async def action_paste_output(request: Request) -> RedirectResponse:
    """POST /epics/{epic_id}/requirements/{req_id}/paste-output — paste CLI output to create evidence and auto-satisfy.

    Creates a command_output evidence record from the pasted stdout/stderr/exit_code,
    then marks the requirement as fulfilled.
    """
    conn = request.state.conn
    epic_id = request.path_params["epic_id"]
    req_id = request.path_params["req_id"]
    form = await request.form()

    # Load the requirement to get the suggested command
    requirement = load_evidence_requirement_by_id(conn, req_id)
    if requirement is None:
        return _error_redirect(request, epic_id, "Requirement not found.")
    if requirement.epic_id != epic_id:
        return _error_redirect(
            request, epic_id, "Requirement does not belong to this epic."
        )
    if requirement.status != "pending":
        return _error_redirect(request, epic_id, "Requirement is not pending.")

    stdout = form.get("stdout", "").strip() or None
    stderr = form.get("stderr", "").strip() or None
    exit_code = form.get("exit_code", "").strip() or None

    command = requirement.suggested_command or form.get("command", "").strip() or None

    # Build title from command or description
    title = (
        f"Output: {command}"
        if command
        else f"Output for: {requirement.description[:60]}"
    )

    # Store exit code as structured data for machine-readability
    structured_data = {"exit_code": exit_code} if exit_code else None

    evidence = BackendEvidence(
        id=generate_id(),
        epic_id=epic_id,
        evidence_type="command_output",
        title=title,
        command=command,
        stdout=stdout,
        stderr=stderr,
        structured_data=structured_data,
    )

    evidence_id = insert_backend_evidence(conn, evidence)
    fulfill_evidence_requirement(conn, req_id, evidence_id)
    conn.commit()

    return RedirectResponse(
        str(request.url_for("epic_detail", epic_id=epic_id)), status_code=303
    )


async def action_delete_evidence(request: Request) -> RedirectResponse:
    """POST /epics/{epic_id}/evidence/{evidence_id}/delete — delete an evidence record."""
    conn = request.state.conn
    epic_id = request.path_params["epic_id"]
    evidence_id = request.path_params["evidence_id"]

    evidence = load_backend_evidence_by_id(conn, evidence_id)
    if evidence is None:
        return _error_redirect(request, epic_id, "Evidence record not found.")
    if evidence.epic_id != epic_id:
        return _error_redirect(
            request, epic_id, "Evidence does not belong to this epic."
        )

    # Clear any requirements that reference this evidence before deleting
    clear_evidence_from_requirements(conn, evidence_id)
    delete_backend_evidence(conn, evidence_id)
    conn.commit()

    return RedirectResponse(
        str(request.url_for("epic_detail", epic_id=epic_id)), status_code=303
    )
