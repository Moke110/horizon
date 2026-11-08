"""Host-run config base: recorded ``dsh`` path, Agent-run defaults, redaction.

The Console (ADR 0009) is the only resident component; everything its Agent runs
need is recorded configuration, so these tests stay offline: no real ``dsh``, no
PATH fallback, no network or credentials.
"""

import json
import os
from pathlib import Path

import pytest

from hostrun import base


def _write_config(home: Path, config: dict) -> Path:
    path = Path(home) / "install" / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    return path


def _dsh_file(directory: Path, name: str | None = None) -> Path:
    binary = Path(directory) / (name or ("dsh.cmd" if os.name == "nt" else "dsh"))
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_text("", encoding="utf-8")
    return binary


def _repair_hint(home: Path) -> str:
    return str(Path(home) / "install" / "config.json")


# ---------------------------------------------------------------- defaults
def test_default_config_records_dsh_and_agent_run_defaults(tmp_path):
    config = base.default_config(tmp_path / "home")

    assert config["paths"]["dsh"] == ""
    assert config["agent_run"] == {
        "feedback_lease_minutes": 45,
        "derived_timeout_seconds": 1200,
        "dsh_args": [],
    }
    assert "listener" not in config
    assert "schedules" not in config


def test_load_config_fills_agent_run_defaults_without_overwriting(tmp_path):
    home = tmp_path / "home"
    _write_config(
        home,
        {
            "schema_version": 1,
            "paths": {"dsh": "D:/tools/dsh.cmd"},
            "agent_run": {"feedback_lease_minutes": 10},
        },
    )

    config = base.load_config(home)

    # recorded values win ...
    assert config["paths"]["dsh"] == "D:/tools/dsh.cmd"
    assert config["agent_run"]["feedback_lease_minutes"] == 10
    # ... and the rest of the section is filled from the defaults.
    assert config["agent_run"]["derived_timeout_seconds"] == 1200
    assert config["agent_run"]["dsh_args"] == []
    # unrelated sections keep their defaults too.
    assert config["paths"]["python"]


def test_legacy_listener_config_is_mapped_onto_agent_run(tmp_path):
    """A migrated install keeps its overlay without the retired section."""
    home = tmp_path / "home"
    _write_config(
        home,
        {
            "schema_version": 1,
            "listener": {
                "start_command": "C:/old/horizon-listener.cmd",
                "stop_command": "C:/old/horizon-listener-stop.cmd",
                "dsh_args": ["--patch", "C:/overlay.yml"],
                "feedback_lease_minutes": 30,
            },
            "schedules": {"enabled": True, "tasks": [{"id": "horizon-daily"}]},
        },
    )

    config = base.load_config(home)

    assert config["agent_run"]["dsh_args"] == ["--patch", "C:/overlay.yml"]
    assert config["agent_run"]["feedback_lease_minutes"] == 30
    assert config["agent_run"]["derived_timeout_seconds"] == 1200
    assert "listener" not in config
    assert "schedules" not in config


def test_agent_run_and_dsh_round_trip(tmp_path):
    home = tmp_path / "home"

    base.update_config(
        home,
        paths={"dsh": "C:/tools/dsh.cmd"},
        agent_run={"dsh_args": ["--patch", "C:/overlay.yml"]},
    )
    loaded = base.load_config(home)

    assert loaded["paths"]["dsh"] == "C:/tools/dsh.cmd"
    assert loaded["agent_run"]["dsh_args"] == ["--patch", "C:/overlay.yml"]


def test_redaction_masks_credentials_but_keeps_paths_and_agent_run(tmp_path):
    home = tmp_path / "home"
    config = base.default_config(home)
    config["paths"]["dsh"] = "C:/tools/dsh.cmd"
    config["paths"]["lark_cli"] = "C:/tools/lark-cli.cmd"
    config["app_secret"] = "s3cret"
    config["agent_run"]["dsh_args"] = ["--patch", "C:/overlay.yml"]

    base.save_config(home, config)

    written = json.loads((home / "install" / "config.json").read_text(encoding="utf-8"))
    assert written["app_secret"] == "***"
    assert written["paths"]["dsh"] == "C:/tools/dsh.cmd"
    assert written["paths"]["lark_cli"] == "C:/tools/lark-cli.cmd"
    assert written["agent_run"]["dsh_args"] == ["--patch", "C:/overlay.yml"]


# -------------------------------------------------------------------- dsh
def test_dsh_bin_returns_empty_when_unrecorded_even_when_on_path(tmp_path, monkeypatch):
    home = tmp_path / "home"
    on_path = tmp_path / "path-bin"
    _dsh_file(on_path)
    monkeypatch.setenv("PATH", str(on_path))

    assert base.dsh_bin(home) == ""


def test_dsh_bin_rejects_relative_recorded_path(tmp_path):
    home = tmp_path / "home"
    _dsh_file(tmp_path / "tools")
    base.update_config(home, paths={"dsh": "tools/dsh.cmd"})

    assert base.dsh_bin(home) == ""


def test_dsh_bin_rejects_missing_recorded_file(tmp_path):
    home = tmp_path / "home"
    base.update_config(home, paths={"dsh": str(tmp_path / "tools" / "gone.cmd")})

    assert base.dsh_bin(home) == ""


def test_dsh_bin_returns_recorded_absolute_file(tmp_path):
    home = tmp_path / "home"
    binary = _dsh_file(tmp_path / "tools")
    base.update_config(home, paths={"dsh": str(binary)})

    assert base.dsh_bin(home) == str(binary)


def test_require_dsh_returns_recorded_absolute_file(tmp_path):
    home = tmp_path / "home"
    binary = _dsh_file(tmp_path / "tools")
    base.update_config(home, paths={"dsh": str(binary)})

    assert base.require_dsh(home) == str(binary)


def test_require_dsh_explains_repair_when_unrecorded(tmp_path):
    home = tmp_path / "home"

    with pytest.raises(base.HostRunError) as excinfo:
        base.require_dsh(home)

    message = str(excinfo.value)
    assert "paths.dsh" in message
    assert _repair_hint(home) in message


def test_require_dsh_explains_repair_when_relative(tmp_path):
    home = tmp_path / "home"
    _dsh_file(tmp_path / "tools")
    base.update_config(home, paths={"dsh": "tools/dsh.cmd"})

    with pytest.raises(base.HostRunError) as excinfo:
        base.require_dsh(home)

    message = str(excinfo.value)
    assert "paths.dsh" in message
    assert "不是绝对路径" in message
    assert _repair_hint(home) in message


def test_require_dsh_explains_repair_when_missing(tmp_path):
    home = tmp_path / "home"
    gone = tmp_path / "tools" / "gone.cmd"
    base.update_config(home, paths={"dsh": str(gone)})

    with pytest.raises(base.HostRunError) as excinfo:
        base.require_dsh(home)

    message = str(excinfo.value)
    assert "paths.dsh" in message
    assert str(gone) in message
    assert _repair_hint(home) in message
