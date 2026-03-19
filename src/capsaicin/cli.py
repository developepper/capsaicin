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


if __name__ == "__main__":
    cli()
