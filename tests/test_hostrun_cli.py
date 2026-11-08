"""Console CLI surface: the installer, control, dispatch, exit codes and docs.

Everything here is offline: no real ``dsh``, no lark-cli and no process is
started.  The retired listener/smoke/schedules/cleanup subcommands are checked
to be gone (#91), not to keep working.
"""

import json
import os
from pathlib import Path

import pytest

import hostrun
from hostrun import cli
from hostrun.base import HostRunError, load_config
from conftest import cli_locations

ROOT = Path(__file__).resolve().parents[1]


def _dsh_file(tmp_path):
    dsh = tmp_path / "tools" / ("dsh.cmd" if os.name == "nt" else "dsh")
    dsh.parent.mkdir(parents=True, exist_ok=True)
    dsh.write_text("", encoding="utf-8")
    return dsh


def _home(tmp_path):
    home = tmp_path / "code" / ".horizon"
    home.mkdir(parents=True, exist_ok=True)
    return home


# ------------------------------------------------------------------- parser
def test_console_control_actions_are_parsed_after_the_location_flags():
    parser = cli.build_parser()

    for action in ("start", "stop", "status", "push"):
        args = parser.parse_args(
            cli._normalize(
                ["--code", "C", "--archive", "A", "console", "control", action]
            )
        )
        assert args.cmd == "console"
        assert args.action == "control"
        assert args.control_action == action

    after = parser.parse_args(
        cli._normalize(["console", "control", "status", "--code", "C", "--archive", "A"])
    )
    assert after.code == "C"
    assert after.archive == "A"


def test_console_install_flags_are_parsed():
    args = cli.build_parser().parse_args(["console", "install", "--python", "P"])
    assert args.python_path == "P"
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["console", "install", "--dsh", "legacy"])


@pytest.mark.parametrize("command", ["listener", "smoke", "cleanup", "schedules"])
def test_retired_subcommands_are_gone(command):
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(cli._normalize([command]))


def test_run_rejects_the_retired_listener_check_intent():
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(
            cli._normalize(["run", "--intent", "listener-check"])
        )


# ---------------------------------------------------------- console install
def test_console_install_uses_bundled_agent_without_starting_it(tmp_path, capsys):
    home = _home(tmp_path)
    (home.parent / "agent/dist").mkdir(parents=True)
    (home.parent / "agent/dist/main.js").write_text("")
    (home.parent / "runtime").mkdir()
    (home.parent / "runtime/node.exe").write_text("")
    assert cli.main([*cli_locations(home), "console", "install"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["agent"]["ok"]
    assert payload["processes_started"] is False
    assert (home.parent / "bin/horizon-console.cmd").is_file()
    assert load_config(home)["host"]["mode"] == "embedded-pi"


def test_console_install_reports_missing_bundled_agent(tmp_path, capsys):
    home = _home(tmp_path)
    assert cli.main([*cli_locations(home), "console", "install"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert "Agent" in payload["note"]
    assert (home.parent / "bin/horizon-console.cmd").is_file()


# ------------------------------------------------------------------- serve
def test_console_serve_dispatches_the_resolved_layout(tmp_path, monkeypatch, capsys):
    home = _home(tmp_path)
    seen = {}

    def fake_serve(home_arg, **kwargs):
        seen["home"] = Path(home_arg)
        seen["resolved"] = kwargs.get("resolved")
        return {"ok": True, "reason": "stopped"}

    monkeypatch.setattr(cli.console_mod, "serve", fake_serve)

    code = cli.main([*cli_locations(home), "console"])

    assert code == 0
    assert seen["home"] == home.resolve()
    assert seen["resolved"]["code"] == str(home.parent.resolve())
    assert json.loads(capsys.readouterr().out)["reason"] == "stopped"


# ----------------------------------------------------------------- failures
def test_dispatch_errors_are_json_not_tracebacks(tmp_path, monkeypatch, capsys):
    home = _home(tmp_path)

    def exploding_serve(home_arg, **kwargs):
        raise HostRunError("boom")

    monkeypatch.setattr(cli.console_mod, "serve", exploding_serve)

    code = cli.main([*cli_locations(home), "console"])

    captured = capsys.readouterr()
    assert code == 1
    payload = json.loads(captured.err)
    assert payload["ok"] is False
    assert "boom" in payload["error"]
    assert "Traceback" not in captured.err


# ---------------------------------------------------------------- docstrings
def test_cli_docstring_names_the_console_as_the_only_resident_component():
    doc = cli.__doc__ or ""

    assert "Console" in doc
    assert "only resident component is the Console" in doc
    assert "listener serve" not in doc


def test_package_docstring_drops_the_retired_modules():
    doc = hostrun.__doc__ or ""

    assert "user-started Console" in doc
    for retired in ("``listener``", "``smoke``", "``cleanup``", "``schedules``"):
        assert retired not in doc
    assert "listener-check" not in doc
