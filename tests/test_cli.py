import json

import pytest
from click.testing import CliRunner
from ollama_stub import OllamaStub, Reply

from pam import __version__
from pam.cli import cli, main
from pam.errors import EXIT_EXPECTED, EXIT_OK, PamNetworkError


def test_version_flag():
    result = CliRunner().invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_bare_pam_prints_the_status_guide_not_the_flag_dump():
    """UX 3, and the end of M1's placeholder.

    `pam` with no args printed `ctx.get_help()` from M1 to M6 Task 4, with a comment
    saying M2+. UX 3 is explicit that it is "a status-aware guide: what state the
    archive is in and the 1-3 sensible next commands - not a flag dump (that's `pam
    --help`)". Asserted in BOTH directions, because a guide that also dumped the flags
    would satisfy a one-sided check while being exactly what UX 3 rules out.
    """
    result = CliRunner().invoke(cli, [])
    assert result.exit_code == 0
    assert "Usage:" not in result.output
    assert "pam has not scanned any photo folder yet." in result.output
    assert "Next: pam scan <your photo folder>" in result.output


# --- M10 Task 7: `pam models pull` (design 12 rev 17, amendment A9) -------------------
#
# EVERY TEST HERE DRIVES `tests/ollama_stub.py`, NEVER THE REAL OLLAMA. The real one is
# installed on this machine, and a pull against it would download gigabytes from a test
# run - `tests/conftest.py`'s `_no_test_talks_to_the_real_ollama` makes forgetting loud
# rather than expensive.
#
# The stub's port is injected by replacing `pam.cli.pull_model` with a call to the REAL
# production function on the stub's port. The production code path is unchanged - the
# same `LoopbackHttpClient`, the same request, the same parsing - and only the port moves,
# because the command deliberately takes no `--archive` and therefore no configured port.


def _pull_at(monkeypatch, real_ollama, stub):
    """Point `pam models pull` at the stub, keeping the real production function."""
    monkeypatch.setattr(
        "pam.cli.pull_model",
        lambda model, **kwargs: real_ollama.pull(model, port=stub.port, timeout=10.0),
    )


def test_pam_models_pull_asks_the_local_ollama_and_says_what_it_sent(monkeypatch, real_ollama):
    """The happy path, asserted on the REQUEST as well as on the output.

    Spec 12 rev 17 fixes three things about what goes on the wire and each is checked
    here rather than inferred from the exit code: `POST`, `/api/pull`, and
    `"stream": false` - the last because `LoopbackHttpClient` reads a whole response and
    a streaming pull would hang until the download finished with nothing consuming it.
    """
    with OllamaStub({"/api/pull": [Reply(body={"status": "success"})]}) as stub:
        _pull_at(monkeypatch, real_ollama, stub)
        result = CliRunner().invoke(cli, ["models", "pull", "qwen2.5vl"])

    assert result.exit_code == 0, result.output
    (sent,) = stub.requests
    assert (sent.method, sent.path) == ("POST", "/api/pull")
    assert json.loads(sent.body) == {"model": "qwen2.5vl", "stream": False}
    assert "ok qwen2.5vl is installed" in result.output


def test_the_warning_is_printed_before_the_request_is_made(monkeypatch, real_ollama):
    """UX 2 forbids a silent multi-minute wait, and BEFORE is the whole requirement.

    A message printed after the response would be no warning at all, and asserting only
    that the words appear in the output cannot tell the two apart. So the stub blocks
    until an event is set, the test asserts the warning is already on screen while the
    request is still in flight, and only then releases it. It waits on an EVENT with a
    deadline rather than sleeping: a fixed sleep re-loses this race on a slower runner,
    which is how CLAUDE.md's hosted-CI lesson was learned.
    """
    import threading
    import time

    import click

    released = threading.Event()
    idle = threading.Event()  # never set: `wait` is the yield, the deadline is the guard
    seen = []
    printed_by_then = []
    reply = Reply(body={"status": "success"}, block_until=released)

    # WATCH THE CALL, NOT THE FINISHED OUTPUT (M8's M697, and this test's own M1554).
    # `result.output` is assembled after the command returns, so its ORDER is the same
    # whether the warning was printed before the wait or after it - a survivor proved
    # exactly that. So every `click.echo` is recorded as it happens, and the barrier
    # thread snapshots what had been printed at the moment the request arrived.
    echoed = []
    real_echo = click.echo

    def recording_echo(message="", *args, **kwargs):
        echoed.append(str(message))
        return real_echo(message, *args, **kwargs)

    monkeypatch.setattr(click, "echo", recording_echo)

    with OllamaStub({"/api/pull": [reply]}) as stub:
        _pull_at(monkeypatch, real_ollama, stub)
        runner = CliRunner()

        def release_once_the_request_arrives():
            # A BARRIER THAT WAITS FOR THE EFFECT AND GIVES UP AT A DEADLINE, which is
            # `tests/test_webui_server.py::await_log`'s pattern and the opposite of a
            # fixed sleep - a sleep re-loses this race on a slower runner and turns a red
            # gate into an intermittent one. The stub records a request BEFORE it answers
            # (see `tests/ollama_stub.py`), so this cannot pass until the CLI has really
            # blocked inside `pull_model`. The release happens either way, so a failure
            # is an assertion below rather than a hung suite.
            deadline = time.monotonic() + 30.0
            while not stub.requests and time.monotonic() < deadline:
                idle.wait(0.005)
            seen.append(bool(stub.requests))
            printed_by_then.append(list(echoed))
            released.set()

        waiter = threading.Thread(target=release_once_the_request_arrives, daemon=True)
        waiter.start()
        result = runner.invoke(cli, ["models", "pull", "qwen2.5vl"])
        waiter.join(timeout=30)

    assert seen == [True], "the stub never saw the request; nothing was proven about order"
    # THE ASSERTION THAT ACTUALLY STATES THE REQUIREMENT: at the instant the server had
    # the request in hand and pam was blocked waiting for it, the warning was already on
    # the user's screen.
    assert any("gigabytes" in message for message in printed_by_then[0]), (
        "pam models pull blocked on the download before telling the user anything."
        " UX 2 forbids a silent multi-minute wait, and `LoopbackHttpClient` cannot show"
        f" progress. Printed by the time the request arrived: {printed_by_then[0]}"
    )
    assert result.exit_code == 0, result.output
    # The warning has to come before the success line in the stream the user reads.
    assert result.output.index("gigabytes") < result.output.index("ok qwen2.5vl is installed")


def test_pam_models_pull_defaults_to_the_model_spec_11_names(monkeypatch, real_ollama):
    """`[MODEL]` is optional (spec 12), and the default is spec 11's caption model.

    The expected name is a literal, written from spec 11 rather than read back from
    `pam.doctor.DEFAULT_CAPTION_MODEL` - a test that compared production against itself
    would pass for any value of it.
    """
    with OllamaStub({"/api/pull": [Reply(body={"status": "success"})]}) as stub:
        _pull_at(monkeypatch, real_ollama, stub)
        result = CliRunner().invoke(cli, ["models", "pull"])

    assert result.exit_code == 0, result.output
    assert json.loads(stub.requests[0].body)["model"] == "qwen2.5vl"


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        (
            Reply(status=500, body={"error": "pull model manifest: file does not exist"}),
            "manifest",
        ),
        (Reply(status=404, body=b"not found"), "404"),
        (Reply(body={"error": "no space left on device"}), "no space left"),
    ],
    ids=["http-500-with-ollamas-reason", "http-404-unparseable-body", "http-200-with-an-error"],
)
def test_a_failed_pull_is_one_line_and_exit_1(monkeypatch, real_ollama, reply, expected):
    """Spec 12: "On failure, the `PamNetworkError` message and exit 1" - spec 13's contract.

    THE HTTP-200-WITH-AN-ERROR ROW IS THE ONE THAT MATTERS. Ollama answers some failures
    with a 200 and an `error` field, so a check that only looked at the status code would
    report a successful pull of a model that is not there - and `pam doctor` would then
    contradict the command that had just claimed to fix it.
    """
    with OllamaStub({"/api/pull": [reply]}) as stub:
        _pull_at(monkeypatch, real_ollama, stub)
        result = CliRunner().invoke(cli, ["models", "pull", "qwen2.5vl"], standalone_mode=False)

    assert isinstance(result.exception, PamNetworkError)
    assert expected in str(result.exception)


def test_a_failed_pull_exits_1_through_the_real_entry_point(monkeypatch, real_ollama):
    """The exit code itself, through `pam.cli.main` rather than through CliRunner.

    `PamNetworkError` is a `PamError`, and spec 13 says one clear line and exit 1. Pinned
    here because the parametrized rows above catch the exception before the mapping.
    """
    with OllamaStub({"/api/pull": [Reply(status=500, body={"error": "nope"})]}) as stub:
        _pull_at(monkeypatch, real_ollama, stub)
        assert main(["models", "pull", "qwen2.5vl"]) == EXIT_EXPECTED


def test_a_successful_pull_exits_0_through_the_real_entry_point(monkeypatch, real_ollama):
    with OllamaStub({"/api/pull": [Reply(body={"status": "success"})]}) as stub:
        _pull_at(monkeypatch, real_ollama, stub)
        assert main(["models", "pull", "qwen2.5vl"]) == EXIT_OK


def test_pam_models_is_not_a_dead_end():
    """UX 1.4. A group invoked bare prints its subcommands rather than nothing at all."""
    result = CliRunner().invoke(cli, ["models"])
    assert "pull" in result.output
