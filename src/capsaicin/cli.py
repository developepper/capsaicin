import click

from capsaicin.init import init_project


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

    from capsaicin.config import ConfigError, resolve_project
    from capsaicin.db import get_connection
    from capsaicin.ticket_add import (
        _get_project_id,
        add_ticket_from_file,
        add_ticket_inline,
    )

    if from_file and title:
        raise click.ClickException("Cannot use both --title and --from.")

    if not from_file and not title:
        raise click.ClickException("Provide --title and --description, or --from FILE.")

    # Resolve project
    if repo_path is None:
        repo_path = str(Path.cwd().resolve())
    else:
        repo_path = str(Path(repo_path).resolve())

    capsaicin_root = Path(repo_path) / ".capsaicin"

    if project_slug:
        slug = project_slug
        project_dir = capsaicin_root / "projects" / slug
        if not project_dir.is_dir():
            raise click.ClickException(f"Project '{slug}' not found at {project_dir}")
    else:
        try:
            slug = resolve_project(capsaicin_root)
        except ConfigError as e:
            raise click.ClickException(str(e))

    project_dir = capsaicin_root / "projects" / slug
    db_path = project_dir / "capsaicin.db"
    log_path = project_dir / "activity.log"

    conn = get_connection(db_path)
    try:
        project_id = _get_project_id(conn)

        if from_file:
            try:
                ticket_id = add_ticket_from_file(
                    conn, project_id, Path(from_file), log_path
                )
            except ValueError as e:
                raise click.ClickException(str(e))
        else:
            if not desc:
                raise click.ClickException(
                    "--description is required when using --title."
                )
            ticket_id = add_ticket_inline(
                conn, project_id, title, desc, list(criteria), log_path
            )

        # Print brief
        row = conn.execute(
            "SELECT id, title, status FROM tickets WHERE id = ?", (ticket_id,)
        ).fetchone()
        ac_count = conn.execute(
            "SELECT COUNT(*) FROM acceptance_criteria WHERE ticket_id = ?", (ticket_id,)
        ).fetchone()[0]
        click.echo(f"Ticket {row['id']}")
        click.echo(f"  Title: {row['title']}")
        click.echo(f"  Status: {row['status']}")
        click.echo(f"  Criteria: {ac_count}")
    finally:
        conn.close()


@ticket.command("dep")
@click.argument("ticket_id")
@click.option(
    "--on", "depends_on_id", required=True, help="ID of the dependency ticket."
)
@click.option("--repo", "repo_path", default=None, help="Path to the repository.")
@click.option("--project", "project_slug", default=None, help="Project slug.")
def ticket_dep(ticket_id, depends_on_id, repo_path, project_slug):
    """Add a dependency between tickets."""
    from pathlib import Path

    from capsaicin.config import ConfigError, resolve_project
    from capsaicin.db import get_connection
    from capsaicin.ticket_dep import add_dependency

    if repo_path is None:
        repo_path = str(Path.cwd().resolve())
    else:
        repo_path = str(Path(repo_path).resolve())

    capsaicin_root = Path(repo_path) / ".capsaicin"

    if project_slug:
        slug = project_slug
        project_dir = capsaicin_root / "projects" / slug
        if not project_dir.is_dir():
            raise click.ClickException(f"Project '{slug}' not found at {project_dir}")
    else:
        try:
            slug = resolve_project(capsaicin_root)
        except ConfigError as e:
            raise click.ClickException(str(e))

    project_dir = capsaicin_root / "projects" / slug
    db_path = project_dir / "capsaicin.db"

    conn = get_connection(db_path)
    try:
        add_dependency(conn, ticket_id, depends_on_id)
        click.echo(f"Dependency added: {ticket_id} depends on {depends_on_id}")
    except ValueError as e:
        raise click.ClickException(str(e))
    finally:
        conn.close()


@ticket.command("run")
@click.argument("ticket_id", required=False, default=None)
@click.option("--repo", "repo_path", default=None, help="Path to the repository.")
@click.option("--project", "project_slug", default=None, help="Project slug.")
def ticket_run_cmd(ticket_id, repo_path, project_slug):
    """Run the implementation pipeline for a ticket."""
    from pathlib import Path

    from capsaicin.adapters.claude_code import ClaudeCodeAdapter
    from capsaicin.config import (
        ConfigError,
        load_config,
        refresh_config_snapshot,
        resolve_project,
    )
    from capsaicin.db import get_connection
    from capsaicin.ticket_run import run_implementation_pipeline, select_ticket

    if repo_path is None:
        repo_path = str(Path.cwd().resolve())
    else:
        repo_path = str(Path(repo_path).resolve())

    capsaicin_root = Path(repo_path) / ".capsaicin"

    if project_slug:
        slug = project_slug
        project_dir = capsaicin_root / "projects" / slug
        if not project_dir.is_dir():
            raise click.ClickException(f"Project '{slug}' not found at {project_dir}")
    else:
        try:
            slug = resolve_project(capsaicin_root)
        except ConfigError as e:
            raise click.ClickException(str(e))

    project_dir = capsaicin_root / "projects" / slug
    db_path = project_dir / "capsaicin.db"
    config_path = project_dir / "config.toml"
    log_path = project_dir / "activity.log"

    try:
        config = load_config(config_path)
    except ConfigError as e:
        raise click.ClickException(str(e))

    conn = get_connection(db_path)
    try:
        refresh_config_snapshot(conn, config)

        # Select ticket
        try:
            ticket = select_ticket(conn, ticket_id)
        except ValueError as e:
            raise click.ClickException(str(e))

        project_id = ticket["project_id"]
        click.echo(
            f"Running implementation for ticket {ticket['id']}: {ticket['title']}"
        )

        adapter = ClaudeCodeAdapter(command=config.implementer.command)
        final_status = run_implementation_pipeline(
            conn=conn,
            project_id=project_id,
            ticket=ticket,
            config=config,
            adapter=adapter,
            log_path=log_path,
        )

        click.echo(f"Ticket {ticket['id']} -> {final_status}")

        # Diagnostic output for human-gate outcomes (T02)
        if final_status == "human-gate":
            from capsaicin.diagnostics import build_run_outcome_message

            diagnostic = build_run_outcome_message(conn, ticket["id"])
            if diagnostic:
                click.echo()
                click.echo(diagnostic)
    finally:
        conn.close()


@ticket.command("review")
@click.argument("ticket_id", required=False, default=None)
@click.option(
    "--allow-drift", is_flag=True, default=False, help="Accept workspace drift."
)
@click.option("--repo", "repo_path", default=None, help="Path to the repository.")
@click.option("--project", "project_slug", default=None, help="Project slug.")
def ticket_review_cmd(ticket_id, allow_drift, repo_path, project_slug):
    """Run the review pipeline for a ticket."""
    from pathlib import Path

    from capsaicin.adapters.claude_code import ClaudeCodeAdapter
    from capsaicin.config import (
        ConfigError,
        load_config,
        refresh_config_snapshot,
        resolve_project,
    )
    from capsaicin.db import get_connection
    from capsaicin.review_baseline import WorkspaceDriftError
    from capsaicin.ticket_review import run_review_pipeline, select_review_ticket

    if repo_path is None:
        repo_path = str(Path.cwd().resolve())
    else:
        repo_path = str(Path(repo_path).resolve())

    capsaicin_root = Path(repo_path) / ".capsaicin"

    if project_slug:
        slug = project_slug
        project_dir = capsaicin_root / "projects" / slug
        if not project_dir.is_dir():
            raise click.ClickException(f"Project '{slug}' not found at {project_dir}")
    else:
        try:
            slug = resolve_project(capsaicin_root)
        except ConfigError as e:
            raise click.ClickException(str(e))

    project_dir = capsaicin_root / "projects" / slug
    db_path = project_dir / "capsaicin.db"
    config_path = project_dir / "config.toml"
    log_path = project_dir / "activity.log"

    try:
        config = load_config(config_path)
    except ConfigError as e:
        raise click.ClickException(str(e))

    conn = get_connection(db_path)
    try:
        refresh_config_snapshot(conn, config)

        # Select ticket
        try:
            ticket = select_review_ticket(conn, ticket_id)
        except ValueError as e:
            raise click.ClickException(str(e))

        project_id = ticket["project_id"]
        click.echo(f"Running review for ticket {ticket['id']}: {ticket['title']}")

        adapter = ClaudeCodeAdapter(command=config.reviewer.command)
        try:
            final_status = run_review_pipeline(
                conn=conn,
                project_id=project_id,
                ticket=ticket,
                config=config,
                adapter=adapter,
                allow_drift=allow_drift,
                log_path=log_path,
            )
        except WorkspaceDriftError as e:
            raise click.ClickException(str(e))

        click.echo(f"Ticket {ticket['id']} -> {final_status}")
    finally:
        conn.close()


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
    from pathlib import Path

    from capsaicin.config import (
        ConfigError,
        load_config,
        refresh_config_snapshot,
        resolve_project,
    )
    from capsaicin.db import get_connection
    from capsaicin.ticket_approve import (
        WorkspaceMismatchError,
        approve_ticket,
        build_approval_summary,
        select_approve_ticket,
    )

    if repo_path is None:
        repo_path = str(Path.cwd().resolve())
    else:
        repo_path = str(Path(repo_path).resolve())

    capsaicin_root = Path(repo_path) / ".capsaicin"

    if project_slug:
        slug = project_slug
        project_dir = capsaicin_root / "projects" / slug
        if not project_dir.is_dir():
            raise click.ClickException(f"Project '{slug}' not found at {project_dir}")
    else:
        try:
            slug = resolve_project(capsaicin_root)
        except ConfigError as e:
            raise click.ClickException(str(e))

    project_dir = capsaicin_root / "projects" / slug
    db_path = project_dir / "capsaicin.db"
    config_path = project_dir / "config.toml"
    log_path = project_dir / "activity.log"

    try:
        config = load_config(config_path)
    except ConfigError as e:
        raise click.ClickException(str(e))

    conn = get_connection(db_path)
    try:
        refresh_config_snapshot(conn, config)

        try:
            ticket = select_approve_ticket(conn, ticket_id)
        except ValueError as e:
            raise click.ClickException(str(e))

        project_id = ticket["project_id"]

        try:
            final_status = approve_ticket(
                conn=conn,
                project_id=project_id,
                ticket=ticket,
                repo_path=config.project.repo_path,
                rationale=rationale,
                force=force,
                log_path=log_path,
            )
        except WorkspaceMismatchError as e:
            raise click.ClickException(str(e))
        except ValueError as e:
            raise click.ClickException(str(e))

        click.echo(f"Ticket {ticket['id']} -> {final_status}")
        click.echo()
        click.echo(build_approval_summary(conn, ticket["id"]))
    finally:
        conn.close()


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
    from pathlib import Path

    from capsaicin.config import ConfigError, resolve_project
    from capsaicin.db import get_connection
    from capsaicin.ticket_revise import revise_ticket, select_revise_ticket

    if repo_path is None:
        repo_path = str(Path.cwd().resolve())
    else:
        repo_path = str(Path(repo_path).resolve())

    capsaicin_root = Path(repo_path) / ".capsaicin"

    if project_slug:
        slug = project_slug
        project_dir = capsaicin_root / "projects" / slug
        if not project_dir.is_dir():
            raise click.ClickException(f"Project '{slug}' not found at {project_dir}")
    else:
        try:
            slug = resolve_project(capsaicin_root)
        except ConfigError as e:
            raise click.ClickException(str(e))

    project_dir = capsaicin_root / "projects" / slug
    db_path = project_dir / "capsaicin.db"
    log_path = project_dir / "activity.log"

    conn = get_connection(db_path)
    try:
        try:
            ticket = select_revise_ticket(conn, ticket_id)
        except ValueError as e:
            raise click.ClickException(str(e))

        project_id = ticket["project_id"]
        findings_list = list(add_findings) if add_findings else None

        final_status = revise_ticket(
            conn=conn,
            project_id=project_id,
            ticket=ticket,
            add_findings=findings_list,
            reset_cycles=reset_cycles,
            log_path=log_path,
        )

        click.echo(f"Ticket {ticket['id']} -> {final_status}")
        if findings_list:
            click.echo(f"  Added {len(findings_list)} finding(s)")
        if reset_cycles:
            click.echo("  Cycle counters reset")
    finally:
        conn.close()


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
    from pathlib import Path

    from capsaicin.config import ConfigError, resolve_project
    from capsaicin.db import get_connection
    from capsaicin.ticket_defer import defer_ticket, select_defer_ticket

    if repo_path is None:
        repo_path = str(Path.cwd().resolve())
    else:
        repo_path = str(Path(repo_path).resolve())

    capsaicin_root = Path(repo_path) / ".capsaicin"

    if project_slug:
        slug = project_slug
        project_dir = capsaicin_root / "projects" / slug
        if not project_dir.is_dir():
            raise click.ClickException(f"Project '{slug}' not found at {project_dir}")
    else:
        try:
            slug = resolve_project(capsaicin_root)
        except ConfigError as e:
            raise click.ClickException(str(e))

    project_dir = capsaicin_root / "projects" / slug
    db_path = project_dir / "capsaicin.db"
    log_path = project_dir / "activity.log"

    conn = get_connection(db_path)
    try:
        try:
            ticket = select_defer_ticket(conn, ticket_id)
        except ValueError as e:
            raise click.ClickException(str(e))

        project_id = ticket["project_id"]

        final_status = defer_ticket(
            conn=conn,
            project_id=project_id,
            ticket=ticket,
            rationale=rationale,
            abandon=abandon,
            log_path=log_path,
        )

        click.echo(f"Ticket {ticket['id']} -> {final_status}")
    finally:
        conn.close()


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
    from pathlib import Path

    from capsaicin.config import ConfigError, resolve_project
    from capsaicin.db import get_connection
    from capsaicin.ticket_unblock import select_unblock_ticket, unblock_ticket

    if repo_path is None:
        repo_path = str(Path.cwd().resolve())
    else:
        repo_path = str(Path(repo_path).resolve())

    capsaicin_root = Path(repo_path) / ".capsaicin"

    if project_slug:
        slug = project_slug
        project_dir = capsaicin_root / "projects" / slug
        if not project_dir.is_dir():
            raise click.ClickException(f"Project '{slug}' not found at {project_dir}")
    else:
        try:
            slug = resolve_project(capsaicin_root)
        except ConfigError as e:
            raise click.ClickException(str(e))

    project_dir = capsaicin_root / "projects" / slug
    db_path = project_dir / "capsaicin.db"
    log_path = project_dir / "activity.log"

    conn = get_connection(db_path)
    try:
        try:
            ticket = select_unblock_ticket(conn, ticket_id)
        except ValueError as e:
            raise click.ClickException(str(e))

        project_id = ticket["project_id"]

        final_status = unblock_ticket(
            conn=conn,
            project_id=project_id,
            ticket=ticket,
            reset_cycles=reset_cycles,
            log_path=log_path,
        )

        click.echo(f"Ticket {ticket['id']} -> {final_status}")
        if reset_cycles:
            click.echo("  Cycle counters reset")
    finally:
        conn.close()


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
    from pathlib import Path

    from capsaicin.config import ConfigError, resolve_project
    from capsaicin.db import get_connection
    from capsaicin.ticket_status import build_project_summary, build_ticket_detail

    if repo_path is None:
        repo_path = str(Path.cwd().resolve())
    else:
        repo_path = str(Path(repo_path).resolve())

    capsaicin_root = Path(repo_path) / ".capsaicin"

    if project_slug:
        slug = project_slug
        project_dir = capsaicin_root / "projects" / slug
        if not project_dir.is_dir():
            raise click.ClickException(f"Project '{slug}' not found at {project_dir}")
    else:
        try:
            slug = resolve_project(capsaicin_root)
        except ConfigError as e:
            raise click.ClickException(str(e))

    project_dir = capsaicin_root / "projects" / slug
    db_path = project_dir / "capsaicin.db"

    conn = get_connection(db_path)
    try:
        if ticket_id:
            try:
                output = build_ticket_detail(conn, ticket_id, verbose=verbose)
            except ValueError as e:
                raise click.ClickException(str(e))
        else:
            project_row = conn.execute("SELECT id FROM projects LIMIT 1").fetchone()
            if project_row is None:
                raise click.ClickException("No project found in database.")
            project_id = project_row["id"]
            output = build_project_summary(conn, project_id)

        click.echo(output)
    finally:
        conn.close()


@cli.command()
@click.option("--repo", "repo_path", default=None, help="Path to the repository.")
@click.option("--project", "project_slug", default=None, help="Project slug.")
def resume(repo_path, project_slug):
    """Resume from interrupted execution."""
    from pathlib import Path

    from capsaicin.adapters.claude_code import ClaudeCodeAdapter
    from capsaicin.config import (
        ConfigError,
        load_config,
        refresh_config_snapshot,
        resolve_project,
    )
    from capsaicin.db import get_connection
    from capsaicin.resume import resume_pipeline

    if repo_path is None:
        repo_path = str(Path.cwd().resolve())
    else:
        repo_path = str(Path(repo_path).resolve())

    capsaicin_root = Path(repo_path) / ".capsaicin"

    if project_slug:
        slug = project_slug
        project_dir = capsaicin_root / "projects" / slug
        if not project_dir.is_dir():
            raise click.ClickException(f"Project '{slug}' not found at {project_dir}")
    else:
        try:
            slug = resolve_project(capsaicin_root)
        except ConfigError as e:
            raise click.ClickException(str(e))

    project_dir = capsaicin_root / "projects" / slug
    db_path = project_dir / "capsaicin.db"
    config_path = project_dir / "config.toml"
    log_path = project_dir / "activity.log"

    try:
        config = load_config(config_path)
    except ConfigError as e:
        raise click.ClickException(str(e))

    conn = get_connection(db_path)
    try:
        refresh_config_snapshot(conn, config)

        project_row = conn.execute("SELECT id FROM projects LIMIT 1").fetchone()
        if project_row is None:
            raise click.ClickException("No project found in database.")
        project_id = project_row["id"]

        impl_adapter = ClaudeCodeAdapter(command=config.implementer.command)
        review_adapter = ClaudeCodeAdapter(command=config.reviewer.command)
        action, detail = resume_pipeline(
            conn=conn,
            project_id=project_id,
            config=config,
            impl_adapter=impl_adapter,
            review_adapter=review_adapter,
            log_path=log_path,
        )

        click.echo(detail)
    finally:
        conn.close()


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
    from pathlib import Path

    from capsaicin.adapters.claude_code import ClaudeCodeAdapter
    from capsaicin.config import (
        ConfigError,
        load_config,
        refresh_config_snapshot,
        resolve_project,
    )
    from capsaicin.db import get_connection
    from capsaicin.loop import run_loop

    if repo_path is None:
        repo_path = str(Path.cwd().resolve())
    else:
        repo_path = str(Path(repo_path).resolve())

    capsaicin_root = Path(repo_path) / ".capsaicin"

    if project_slug:
        slug = project_slug
        project_dir = capsaicin_root / "projects" / slug
        if not project_dir.is_dir():
            raise click.ClickException(f"Project '{slug}' not found at {project_dir}")
    else:
        try:
            slug = resolve_project(capsaicin_root)
        except ConfigError as e:
            raise click.ClickException(str(e))

    project_dir = capsaicin_root / "projects" / slug
    db_path = project_dir / "capsaicin.db"
    config_path = project_dir / "config.toml"
    log_path = project_dir / "activity.log"

    try:
        config = load_config(config_path)
    except ConfigError as e:
        raise click.ClickException(str(e))

    conn = get_connection(db_path)
    try:
        refresh_config_snapshot(conn, config)

        project_row = conn.execute("SELECT id FROM projects LIMIT 1").fetchone()
        if project_row is None:
            raise click.ClickException("No project found in database.")
        project_id = project_row["id"]

        impl_adapter = ClaudeCodeAdapter(command=config.implementer.command)
        review_adapter = ClaudeCodeAdapter(command=config.reviewer.command)
        try:
            final_status, detail = run_loop(
                conn=conn,
                project_id=project_id,
                config=config,
                impl_adapter=impl_adapter,
                review_adapter=review_adapter,
                ticket_id=ticket_id,
                max_cycles=max_cycles,
                log_path=log_path,
            )
        except ValueError as e:
            raise click.ClickException(str(e))

        click.echo(detail)
    finally:
        conn.close()


@cli.command()
@click.option("--repo", "repo_path", default=None, help="Path to the repository.")
@click.option("--project", "project_slug", default=None, help="Project slug.")
def doctor(repo_path, project_slug):
    """Run preflight checks to validate environment and repo setup."""
    from pathlib import Path

    from capsaicin.config import ConfigError, load_config, resolve_project
    from capsaicin.preflight import run_preflight

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
