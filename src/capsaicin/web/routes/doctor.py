"""Doctor route — preflight health checks."""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import HTMLResponse

from capsaicin.config import ConfigError, load_config
from capsaicin.preflight import run_preflight
from capsaicin.web.templating import templates


async def doctor_page(request: Request) -> HTMLResponse:
    """Run preflight checks and render results."""
    config_path = request.app.state.config_path

    # Resolve repo_path and adapter_command from config
    adapter_command = "claude"
    repo_path = None
    workspace_enabled = False
    worktree_root = None
    try:
        config = load_config(config_path)
        repo_path = config.project.repo_path
        adapter_command = config.implementer.command
        workspace_enabled = config.workspace.enabled
        worktree_root = config.workspace.worktree_root
    except (ConfigError, Exception):
        pass

    if repo_path is None:
        # Fallback: cannot run checks without a repo path
        return templates.TemplateResponse(
            request,
            "doctor.html",
            {"report": None, "error": "Could not determine repo path from config."},
        )

    report = run_preflight(
        repo_path,
        adapter_command=adapter_command,
        workspace_enabled=workspace_enabled,
        worktree_root=worktree_root,
    )

    return templates.TemplateResponse(
        request,
        "doctor.html",
        {"report": report, "error": None},
    )
