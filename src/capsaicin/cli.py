import click

from capsaicin.config import ConfigError
from capsaicin.errors import CapsaicinError
from capsaicin.init import init_project
from capsaicin.project_context import resolve_context


def _resolve_or_fail(repo_path, project_slug):
    """Resolve project context, converting ConfigError to ClickException."""
    try:
        return resolve_context(repo_path, project_slug)
    except ConfigError as e:
        raise click.ClickException(str(e))


def _app_context(ctx):
    """Build an AppContext from a ProjectContext."""
    from capsaicin.app.context import AppContext

    return AppContext.from_project_context(ctx)


@click.group()
def cli():
    """Capsaicin — local-first autonomous ticket loop for AI-assisted development."""


@cli.command()
@click.option("--project", "project_name", default=None, help="Project name.")
@click.option("--repo", "repo_path", default=None, help="Path to the repository.")
def init(project_name, repo_path):
    """Initialize a new capsaicin project."""
    if project_name is None:
        project_name = "my-project"
    try:
        project_dir = init_project(project_name, repo_path)
    except ValueError as e:
        raise click.ClickException(str(e))
    click.echo(f"Initialized project at {project_dir}")


@cli.group()
def ticket():
    """Manage tickets."""


@ticket.command("add")
@click.option("--title", default=None, help="Ticket title.")
@click.option("--description", "desc", default=None, help="Ticket description.")
@click.option("--criteria", multiple=True, help="Acceptance criterion (repeatable).")
@click.option(
    "--from",
    "from_file",
    default=None,
    type=click.Path(exists=True),
    help="TOML file to import.",
)
@click.option("--repo", "repo_path", default=None, help="Path to the repository.")
@click.option(
    "--project",
    "project_slug",
    default=None,
    help="Project slug (required when multiple projects exist).",
)
def ticket_add(title, desc, criteria, from_file, repo_path, project_slug):
    """Add a new ticket."""
    from pathlib import Path

    from capsaicin.ticket_add import (
        _get_project_id,
        add_ticket_from_file,
        add_ticket_inline,
    )

    if from_file and title:
        raise click.ClickException("Cannot use both --title and --from.")

    if not from_file and not title:
        raise click.ClickException("Provide --title and --description, or --from FILE.")

    with _resolve_or_fail(repo_path, project_slug) as ctx:
        project_id = _get_project_id(ctx.conn)

        if from_file:
            try:
                ticket_id = add_ticket_from_file(
                    ctx.conn, project_id, Path(from_file), ctx.log_path
                )
            except ValueError as e:
                raise click.ClickException(str(e))
        else:
            if not desc:
                raise click.ClickException(
                    "--description is required when using --title."
                )
            ticket_id = add_ticket_inline(
                ctx.conn, project_id, title, desc, list(criteria), ctx.log_path
            )

        # Print brief
        row = ctx.conn.execute(
            "SELECT id, title, status FROM tickets WHERE id = ?", (ticket_id,)
        ).fetchone()
        ac_count = ctx.conn.execute(
            "SELECT COUNT(*) FROM acceptance_criteria WHERE ticket_id = ?", (ticket_id,)
        ).fetchone()[0]
        click.echo(f"Ticket {row['id']}")
        click.echo(f"  Title: {row['title']}")
        click.echo(f"  Status: {row['status']}")
        click.echo(f"  Criteria: {ac_count}")


@ticket.command("dep")
@click.argument("ticket_id")
@click.option(
    "--on", "depends_on_id", required=True, help="ID of the dependency ticket."
)
@click.option("--repo", "repo_path", default=None, help="Path to the repository.")
@click.option("--project", "project_slug", default=None, help="Project slug.")
def ticket_dep(ticket_id, depends_on_id, repo_path, project_slug):
    """Add a dependency between tickets."""
    from capsaicin.ticket_dep import add_dependency

    with _resolve_or_fail(repo_path, project_slug) as ctx:
        try:
            add_dependency(ctx.conn, ticket_id, depends_on_id)
        except (ValueError, CapsaicinError) as e:
            raise click.ClickException(str(e))
        click.echo(f"Dependency added: {ticket_id} depends on {depends_on_id}")


@ticket.command("run")
@click.argument("ticket_id", required=False, default=None)
@click.option("--repo", "repo_path", default=None, help="Path to the repository.")
@click.option("--project", "project_slug", default=None, help="Project slug.")
def ticket_run_cmd(ticket_id, repo_path, project_slug):
    """Run the implementation pipeline for a ticket."""
    from capsaicin.app.commands.run_ticket import run

    with _resolve_or_fail(repo_path, project_slug) as ctx:
        app = _app_context(ctx)
        app.refresh_config()

        try:
            result = run(
                conn=app.conn,
                project_id=app.project_id,
                config=app.config,
                ticket_id=ticket_id,
                log_path=app.log_path,
            )
        except (ValueError, CapsaicinError) as e:
            raise click.ClickException(str(e))

        click.echo(result.detail)
        click.echo(f"Ticket {result.ticket_id} -> {result.final_status}")

        # Diagnostic output for human-gate outcomes
        if result.final_status == "human-gate":
            from capsaicin.diagnostics import build_run_outcome_message

            diagnostic = build_run_outcome_message(app.conn, result.ticket_id)
            if diagnostic:
                click.echo()
                click.echo(diagnostic)


@ticket.command("review")
@click.argument("ticket_id", required=False, default=None)
@click.option(
    "--allow-drift", is_flag=True, default=False, help="Accept workspace drift."
)
@click.option("--repo", "repo_path", default=None, help="Path to the repository.")
@click.option("--project", "project_slug", default=None, help="Project slug.")
def ticket_review_cmd(ticket_id, allow_drift, repo_path, project_slug):
    """Run the review pipeline for a ticket."""
    from capsaicin.app.commands.review_ticket import review
    from capsaicin.review_baseline import WorkspaceDriftError

    with _resolve_or_fail(repo_path, project_slug) as ctx:
        app = _app_context(ctx)
        app.refresh_config()

        try:
            result = review(
                conn=app.conn,
                project_id=app.project_id,
                config=app.config,
                ticket_id=ticket_id,
                allow_drift=allow_drift,
                log_path=app.log_path,
            )
        except WorkspaceDriftError as e:
            raise click.ClickException(str(e))
        except (ValueError, CapsaicinError) as e:
            raise click.ClickException(str(e))

        click.echo(result.detail)
        click.echo(f"Ticket {result.ticket_id} -> {result.final_status}")


@ticket.command("approve")
@click.argument("ticket_id", required=False, default=None)
@click.option("--rationale", default=None, help="Rationale for approval.")
@click.option(
    "--force", is_flag=True, default=False, help="Override workspace drift check."
)
@click.option("--repo", "repo_path", default=None, help="Path to the repository.")
@click.option("--project", "project_slug", default=None, help="Project slug.")
def ticket_approve_cmd(ticket_id, rationale, force, repo_path, project_slug):
    """Approve a ticket at the human gate."""
    from capsaicin.app.commands.approve_ticket import approve
    from capsaicin.ticket_approve import WorkspaceMismatchError

    with _resolve_or_fail(repo_path, project_slug) as ctx:
        app = _app_context(ctx)
        app.refresh_config()

        try:
            result = approve(
                conn=app.conn,
                project_id=app.project_id,
                config=app.config,
                ticket_id=ticket_id,
                rationale=rationale,
                force=force,
                log_path=app.log_path,
            )
        except WorkspaceMismatchError as e:
            raise click.ClickException(str(e))
        except (ValueError, CapsaicinError) as e:
            raise click.ClickException(str(e))

        click.echo(f"Ticket {result.ticket_id} -> {result.final_status}")
        click.echo()

        from capsaicin.ticket_approve import build_approval_summary

        click.echo(build_approval_summary(app.conn, result.ticket_id))


@ticket.command("revise")
@click.argument("ticket_id", required=False, default=None)
@click.option(
    "--add-finding",
    "add_findings",
    multiple=True,
    help="Human finding description (repeatable).",
)
@click.option(
    "--reset-cycles",
    is_flag=True,
    default=False,
    help="Reset cycle and retry counters.",
)
@click.option("--repo", "repo_path", default=None, help="Path to the repository.")
@click.option("--project", "project_slug", default=None, help="Project slug.")
def ticket_revise_cmd(ticket_id, add_findings, reset_cycles, repo_path, project_slug):
    """Send a ticket back for revision from the human gate."""
    from capsaicin.app.commands.revise_ticket import revise

    with _resolve_or_fail(repo_path, project_slug) as ctx:
        app = _app_context(ctx)

        findings_list = list(add_findings) if add_findings else None

        try:
            result = revise(
                conn=app.conn,
                project_id=app.project_id,
                ticket_id=ticket_id,
                add_findings=findings_list,
                reset_cycles=reset_cycles,
                log_path=app.log_path,
            )
        except (ValueError, CapsaicinError) as e:
            raise click.ClickException(str(e))

        click.echo(f"Ticket {result.ticket_id} -> {result.final_status}")
        if findings_list:
            click.echo(f"  Added {len(findings_list)} finding(s)")
        if reset_cycles:
            click.echo("  Cycle counters reset")


@ticket.command("complete")
@click.argument("ticket_id", required=False, default=None)
@click.option("--rationale", default=None, help="Rationale for completion.")
@click.option("--repo", "repo_path", default=None, help="Path to the repository.")
@click.option("--project", "project_slug", default=None, help="Project slug.")
def ticket_complete_cmd(ticket_id, rationale, repo_path, project_slug):
    """Mark a pr-ready ticket as done."""
    from capsaicin.app.commands.complete_ticket import complete

    with _resolve_or_fail(repo_path, project_slug) as ctx:
        app = _app_context(ctx)

        try:
            result = complete(
                conn=app.conn,
                project_id=app.project_id,
                ticket_id=ticket_id,
                rationale=rationale,
                log_path=app.log_path,
            )
        except (ValueError, CapsaicinError) as e:
            raise click.ClickException(str(e))

        click.echo(f"Ticket {result.ticket_id} -> {result.final_status}")


@ticket.command("defer")
@click.argument("ticket_id", required=False, default=None)
@click.option("--rationale", default=None, help="Rationale for deferral.")
@click.option(
    "--abandon", is_flag=True, default=False, help="Abandon the ticket entirely."
)
@click.option("--repo", "repo_path", default=None, help="Path to the repository.")
@click.option("--project", "project_slug", default=None, help="Project slug.")
def ticket_defer_cmd(ticket_id, rationale, abandon, repo_path, project_slug):
    """Defer or abandon a ticket from the human gate."""
    from capsaicin.app.commands.defer_ticket import defer

    with _resolve_or_fail(repo_path, project_slug) as ctx:
        app = _app_context(ctx)

        try:
            result = defer(
                conn=app.conn,
                project_id=app.project_id,
                ticket_id=ticket_id,
                rationale=rationale,
                abandon=abandon,
                log_path=app.log_path,
            )
        except (ValueError, CapsaicinError) as e:
            raise click.ClickException(str(e))

        click.echo(f"Ticket {result.ticket_id} -> {result.final_status}")


@ticket.command("unblock")
@click.argument("ticket_id")
@click.option(
    "--reset-cycles",
    is_flag=True,
    default=False,
    help="Reset cycle and retry counters.",
)
@click.option("--repo", "repo_path", default=None, help="Path to the repository.")
@click.option("--project", "project_slug", default=None, help="Project slug.")
def ticket_unblock_cmd(ticket_id, reset_cycles, repo_path, project_slug):
    """Unblock a blocked ticket and return it to ready."""
    from capsaicin.app.commands.unblock_ticket import unblock

    with _resolve_or_fail(repo_path, project_slug) as ctx:
        app = _app_context(ctx)

        try:
            result = unblock(
                conn=app.conn,
                project_id=app.project_id,
                ticket_id=ticket_id,
                reset_cycles=reset_cycles,
                log_path=app.log_path,
            )
        except (ValueError, CapsaicinError) as e:
            raise click.ClickException(str(e))

        click.echo(f"Ticket {result.ticket_id} -> {result.final_status}")
        if reset_cycles:
            click.echo("  Cycle counters reset")


@cli.command()
@click.option(
    "--ticket", "ticket_id", default=None, help="Show detail for a specific ticket."
)
@click.option(
    "--verbose", is_flag=True, default=False, help="Include run and transition history."
)
@click.option("--repo", "repo_path", default=None, help="Path to the repository.")
@click.option("--project", "project_slug", default=None, help="Project slug.")
def status(ticket_id, verbose, repo_path, project_slug):
    """Show project or ticket status."""
    from capsaicin.ticket_status import render_dashboard, render_ticket_detail

    with _resolve_or_fail(repo_path, project_slug) as ctx:
        app = _app_context(ctx)

        if ticket_id:
            try:
                output = render_ticket_detail(app.conn, ticket_id, verbose=verbose)
            except ValueError as e:
                raise click.ClickException(str(e))
        else:
            output = render_dashboard(app.conn, app.project_id)

        click.echo(output)


@cli.command()
@click.option("--repo", "repo_path", default=None, help="Path to the repository.")
@click.option("--project", "project_slug", default=None, help="Project slug.")
def resume(repo_path, project_slug):
    """Resume from interrupted execution."""
    from capsaicin.app.commands.resume import resume as resume_cmd

    with _resolve_or_fail(repo_path, project_slug) as ctx:
        app = _app_context(ctx)
        app.refresh_config()

        result = resume_cmd(
            conn=app.conn,
            project_id=app.project_id,
            config=app.config,
            log_path=app.log_path,
        )

        click.echo(result.detail)


@cli.command()
@click.argument("ticket_id", required=False, default=None)
@click.option(
    "--max-cycles",
    "max_cycles",
    type=int,
    default=None,
    help="Override max cycles (default from config).",
)
@click.option("--repo", "repo_path", default=None, help="Path to the repository.")
@click.option("--project", "project_slug", default=None, help="Project slug.")
def loop(ticket_id, max_cycles, repo_path, project_slug):
    """Run the implement-review-revise loop automatically."""
    from capsaicin.app.commands.loop import loop as loop_cmd

    with _resolve_or_fail(repo_path, project_slug) as ctx:
        app = _app_context(ctx)
        app.refresh_config()

        try:
            result = loop_cmd(
                conn=app.conn,
                project_id=app.project_id,
                config=app.config,
                ticket_id=ticket_id,
                max_cycles=max_cycles,
                log_path=app.log_path,
            )
        except (ValueError, CapsaicinError) as e:
            raise click.ClickException(str(e))

        click.echo(result.detail)


@cli.command()
@click.option(
    "--port", type=int, default=None, help="Port to bind to (auto-selects if omitted)."
)
@click.option(
    "--no-open",
    "no_open",
    is_flag=True,
    default=False,
    help="Do not open browser automatically.",
)
@click.option("--repo", "repo_path", default=None, help="Path to the repository.")
@click.option("--project", "project_slug", default=None, help="Project slug.")
def ui(port, no_open, repo_path, project_slug):
    """Launch the local operator web UI."""
    from capsaicin.web.server import run_server

    # Resolve project context, extract the values we need, then close
    # the connection before starting the server.  The web layer opens
    # its own per-request connections — keeping the launcher connection
    # alive would hold a long-lived SQLite handle for the server's
    # entire lifetime, contradicting the request-scoped model.
    with _resolve_or_fail(repo_path, project_slug) as ctx:
        db_path = ctx.db_path
        project_id = _app_context(ctx).project_id
        config_path = ctx.config_path
        log_path = ctx.log_path

    run_server(
        db_path=db_path,
        project_id=project_id,
        config_path=config_path,
        log_path=log_path,
        port=port,
        open_browser=not no_open,
    )


@cli.command()
@click.option("--repo", "repo_path", default=None, help="Path to the repository.")
@click.option("--project", "project_slug", default=None, help="Project slug.")
def doctor(repo_path, project_slug):
    """Run preflight checks to validate environment and repo setup."""
    from pathlib import Path

    from capsaicin.config import load_config, resolve_project

    if repo_path is None:
        repo_path = str(Path.cwd().resolve())
    else:
        repo_path = str(Path(repo_path).resolve())

    capsaicin_root = Path(repo_path) / ".capsaicin"

    # Resolve adapter command from config.  For a validation command,
    # config-resolution failures should surface as errors rather than
    # silently falling back to a default that may be wrong.
    adapter_command = "claude"
    if capsaicin_root.is_dir():
        if project_slug:
            slug = project_slug
            project_dir = capsaicin_root / "projects" / slug
            if not project_dir.is_dir():
                raise click.ClickException(
                    f"Project '{project_slug}' not found at {project_dir}"
                )
        else:
            try:
                slug = resolve_project(capsaicin_root)
            except ConfigError as e:
                raise click.ClickException(str(e))
            project_dir = capsaicin_root / "projects" / slug

        config_path = project_dir / "config.toml"
        if config_path.is_file():
            try:
                config = load_config(config_path)
                adapter_command = config.implementer.command
            except ConfigError as e:
                raise click.ClickException(f"Could not load project config: {e}")

    from capsaicin.preflight import run_preflight

    report = run_preflight(repo_path, adapter_command=adapter_command)

    # Render checklist
    status_icons = {"pass": "OK", "warn": "WARN", "fail": "FAIL"}
    for check in report.checks:
        icon = status_icons[check.status]
        click.echo(f"  [{icon}] {check.message}")
        if check.detail and check.status != "pass":
            for line in check.detail.splitlines():
                click.echo(f"         {line}")

    # Summary
    click.echo()
    if report.passed and not report.has_warnings:
        click.echo("All checks passed.")
    elif report.passed:
        click.echo(f"All checks passed with {len(report.warnings)} warning(s).")
    else:
        click.echo(
            f"{len(report.failures)} check(s) failed. "
            "Fix the issues above before running agent work."
        )

    if not report.passed:
        raise SystemExit(1)


if __name__ == "__main__":
    cli()
