import click
import pytest

from pam.cli import cli, main
from pam.errors import (
    EXIT_EXPECTED,
    EXIT_INTERNAL,
    EXIT_OK,
    ConfigError,
    MigrationError,
    PamError,
    WorkspaceLockedError,
)


def test_exit_constants():
    assert (EXIT_OK, EXIT_EXPECTED, EXIT_INTERNAL) == (0, 1, 2)


def test_hierarchy():
    for exc in (ConfigError, WorkspaceLockedError, MigrationError):
        assert issubclass(exc, PamError)


@pytest.fixture()
def failing_command():
    """Temporarily register a command that raises, then clean it up."""

    def _register(exc: Exception):
        @cli.command(name="_boom")
        def _boom():
            raise exc

        return "_boom"

    yield _register
    cli.commands.pop("_boom", None)


def test_pam_error_exits_1(failing_command, capsys):
    failing_command(PamError("something expected went wrong"))
    assert main(["_boom"]) == EXIT_EXPECTED
    assert "something expected went wrong" in capsys.readouterr().err


def test_unexpected_error_exits_2(failing_command, capsys):
    failing_command(RuntimeError("bug"))
    assert main(["_boom"]) == EXIT_INTERNAL
    assert "RuntimeError" in capsys.readouterr().err


def test_usage_error_exits_2_from_click():
    # click reports unknown commands itself; exit code comes from ClickException
    assert main(["definitely-not-a-command"]) == click.UsageError.exit_code


def test_string_system_exit_prints_and_exits_1(failing_command, capsys):
    failing_command(SystemExit("fatal: something specific"))
    assert main(["_boom"]) == EXIT_EXPECTED
    assert "fatal: something specific" in capsys.readouterr().err
